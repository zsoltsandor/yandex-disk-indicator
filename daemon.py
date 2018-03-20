#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from tools import *

from pyinotify import ProcessEvent, WatchManager, ThreadedNotifier, IN_MODIFY, IN_ACCESS
from threading import Timer as thTimer, enumerate as thList, Lock, Thread

from logging import getLogger
logger = getLogger('')

#################### Main daemon/indicator classes ####################
class YDDaemon(object):         # Yandex.Disk daemon interface
  '''
  This is the fully automated class that serves as daemon interface.
  Public methods:
  __init__ - Handles initialization of the object and as a part - auto-start daemon if it
             is required by configuration settings.
  getOuput - Provides daemon output (in user language when optional parameter userLang is
             True)
  start    - Request to start daemon. Do nothing if it is alreday started
  stop     - Request to stop daemon. Do nothing if it is not started
  exit     - Handles 'Stop on exit' facility according to daemon configuration settings.
  change   - Call-back function for handling daemon status changes outside the class.
             It have to be redefined by UI class.
             The parameters of the call - status values dictionary (see __v description below)

  Class interface variables:
  ID       - the daemon identity string (empty in single daemon configuration)
  config   - The daemon configuration dictionary (object of _DConfig(Config) class)
  __v      - private status values dictionary with following keys:
              'status' - current daemon status
              'progress' - synchronization progress or ''
              'laststatus' - previous daemon status
              'statchg' - True indicates that status was changed
              'total' - total Yandex disk space
              'used' - currently used space
              'free' - available space
              'trash' - size of trash
              'szchg' - True indicates that sizes were changed
              'lastitems' - list of last synchronized items or []
              'lastchg' - True indicates that lastitems was changed
              'error' - error message
              'path' - path of error
  '''
  #################### Virtual classes/methods ##################
  # they have to be implemented in GUI part of code
  
  def errorDialog(self, err):           # Show error messages according to the error
    # it is virtual method
    return 0 

  def change(self, vals):               # Update handler
    logger.debug('Update event: %s \nValues : %s' % (str(update), str(vals)))

  #################### Own classes/methods ####################

  class Watcher(object):              # File changes watcher implementation
    '''
    iNotify watcher object for monitor of changes daemon internal log for the fastest
    reaction on status change.
    '''
    def __init__(self, path, handler, par=None):
      # Watched path
      self.path = path
      # Initialize iNotify watcher
      class _EH(ProcessEvent):           # Event handler class for iNotifier
        def process_IN_MODIFY(self, event):
          handler(par)
      self._watchMngr = WatchManager()   # Create watch manager
      # Create PyiNotifier
      self._iNotifier = ThreadedNotifier(self._watchMngr, _EH(), timeout=0.5)
      self._iNotifier.start()
      self._status = False

    def start(self):               # Activate iNotify watching
      if self._status:
        return
      if not pathExists(self.path):
        logger.info("iNotiy was not started: path '"+self.path+"' was not found.")
        return
      self._watch = self._watchMngr.add_watch(self.path, IN_MODIFY|IN_ACCESS, rec=False)
      self._status = True

    def stop(self):                      # Stop iNotify watching
      if not self._status:
        return
      # Remove watch
      self._watchMngr.rm_watch(self._watch[self.path])
      # Stop timer
      self._iNotifier.stop()
      self._status = False

  class __DConfig(Config):              # Redefined class for daemon config

    def save(self):  # Update daemon config file
      # Make a new Config object
      fileConfig = Config(self.fileName, load=False)
      # Copy values that could be changed to the new Config object and convert representation
      ro = self.get('read-only', False)
      fileConfig['read-only'] = '' if ro else None
      fileConfig['overwrite'] = '' if self.get('overwrite', False) and ro else None
      fileConfig['startonstartofindicator'] = self.get('startonstartofindicator', True)
      fileConfig['stoponexitfromindicator'] = self.get('stoponexitfromindicator', False)
      exList = self.get('exclude-dirs', None)
      fileConfig['exclude-dirs'] = (None if exList is None else
                                    ', '.join([v for v in CVal(exList)]))
      # Store changed values
      fileConfig.save()
      self.changed = False

    def load(self):  # Get daemon config from its config file
      if super().load():                                    # Load config from file
        # Convert values representations
        self['read-only'] = (self.get('read-only', None) == '')
        self['overwrite'] = (self.get('overwrite', None) == '')
        self.setdefault('startonstartofindicator', True)    # New value to start daemon individually
        self.setdefault('stoponexitfromindicator', False)   # New value to stop daemon individually
        exDirs = self.setdefault('exclude-dirs', None)
        if exDirs is not None and not isinstance(exDirs, list):
          # Additional parsing required when quoted value like "dir,dir,dir" is specified.
          # When the value specified without quotes it will be already list value [dir, dir, dir].
          self['exclude-dirs'] = self.getValue(exDirs)
        return True
      else:
        return False

  def __init__(self, cfgFile, ID):      # Check that daemon installed and configured
    '''
    cfgFile  - full path to config file
    ID       - identity string '#<n> ' in multi-instance environment or
               '' in single instance environment'''
    self.ID = ID                                      # Remember daemon identity
    self.__YDC = which('yandex-disk')
    if self.__YDC is None:
      sysExit(_('Yandex.Disk utility is not installed.\n ' +
            'Visit www.yandex.ru, download and install Yandex.Disk daemon.'))
    # Try to read Yandex.Disk configuration file and make sure that it is correctly configured
    self.config = self.__DConfig(cfgFile, load=False)
    while not (self.config.load() and
               pathExists(self.config.get('dir', '')) and
               pathExists(self.config.get('auth', ''))):
      if self.errorDialog(cfgFile) != 0:
        if ID != '':
          self.config['dir'] = ''
          break   # Exit from loop in multi-instance configuration
        else:
          sysExit('Daemon is not configured')
    self.tmpDir = getenv("TMPDIR")
    if self.tmpDir is None:
        self.tmpDir = '/tmp'
    # Initialize watching staff
    self.__watcher = self.Watcher(pathJoin(expanduser(self.config['dir']), '.sync/cli.log'), self.__eventHandler, par=True)
    # Set initial daemon status values
    self.__v = {'status': 'unknown', 'progress': '', 'laststatus': 'unknown', 'statchg': True,
                'total': '...', 'used': '...', 'free': '...', 'trash': '...', 'szchg': True,
                'error':'', 'path':'', 'lastitems': [], 'lastchg': True}
    # Initialize timer staff
    self.__timer = thTimer(0.3, self.__eventHandler, (False,))
    self.__timer.start()
    self.__tCnt = 0
    # Lock for eventHandler (it is critical section that is called by timer and watcher threads)
    self.__lock = Lock()
    if self.config.get('startonstartofindicator', True):
      self.start()                       # Start daemon if it is required
    else:
      self.__watcher.start()             # try to activate file watcher

  def __eventHandler(self, watch):       # Daemon event handler
    '''
    Handle watcher and and timer based events.
    After receiving and parsing the daemon output it raises outside change event if daemon changes
    at least one of its status values.
    It can be called by timer (when watch=False) or by watcher (when watch=True)
    '''

    self.__lock.acquire()                          # It can be called from two different threads
    # Parse fresh daemon output. Parsing returns true when something changed
    if self.__parseOutput(self.getOutput()):
      logger.debug(self.ID + 'Event raised by' + (' Watcher' if watch else ' Timer'))
      self.change(self.__v)                   # Raise outside update event
    # --- Handle timer delays ---
    if watch:                                 # True means that it is called by watcher
      self.__timer.cancel()                       # Cancel timer if it still active
      self.__timer = thTimer(2, self.__eventHandler, (False,))   # Set timer interval to 2 sec.
      self.__timer.start()
      self.__tCnt = 0                             # Reset counter as it was triggered not by timer
    else:                                        # It called by timer
      if self.__v['status'] == 'busy':           # In 'busy' keep update interval (2 sec.)
        self.__timer = thTimer(2, self.__eventHandler, (False,))
        self.__timer.start()
      else:
        if self.__tCnt < 9:                       # Increase interval up to 10 sec (2 + 8)
          self.__timer = thTimer((2 + self.__tCnt), self.__eventHandler, (False,))
          self.__timer.start()
          self.__tCnt += 1                        # Increase counter to increase delay next activation.
    self.__lock.release()

  def getOutput(self, userLang=False):  # Get result of 'yandex-disk status'
    cmd = [self.__YDC, '-c', self.config.fileName, 'status']
    if not userLang:      # Change locale settings when it required
      cmd = ['env', '-i', "LANG='en_US.UTF8'", "TMPDIR=%s"%self.tmpDir] + cmd
    try:
      output = check_output(cmd, universal_newlines=True)
    except:
      output = ''         # daemon is not running or bad
    # logger.debug('output = %s' % output)
    return output

  def RequestOutput(self, callBack):     # Handler for request to disply the daemon output 
    def do_output():
      callBack(self.getOutput(True))
    Thread(None, do_output).start()

  def __parseOutput(self, out):         # Parse the daemon output
    '''
    It parses the daemon output and check that something changed from last daemon status.
    The self.__v dictionary is updated with new daemon statuses. It returns True is something changed

    Daemon status is converted form daemon raw statuses into internal representation.
    Internal status can be on of the following: 'busy', 'idle', 'paused', 'none', 'no_net', 'error'.
    Conversion is done by following rules:
     - empty status (daemon is not running) converted to 'none'
     - statuses 'busy', 'idle', 'paused' are passed 'as is'
     - 'index' is ignored (previous status is kept)
     - 'no internet access' converted to 'no_net'
     - 'error' covers all other errors, except 'no internet access'
    '''
    self.__v['statchg'] = False
    self.__v['szchg'] = False
    self.__v['lastchg'] = False
    # Split output on two parts: list of named values and file list
    output = out.split('Last synchronized items:')
    if len(output) == 2:
      files = output[1]
    else:
      files = ''
    output = output[0].splitlines()
    # Make a dictionary from named values (use only lines containing ':')
    res = dict([reFindall(r'\s*(.+):\s*(.*)', l)[0] for l in output if ':' in l])
    # Parse named status values
    for srch, key in (('Synchronization core status', 'status'), ('Sync progress', 'progress'),
                      ('Total', 'total'), ('Used', 'used'), ('Available', 'free'),
                      ('Trash size', 'trash'), ('Error', 'error'), ('Path', 'path')):
      val = res.get(srch, '')
      if key == 'status':                     # Convert status to internal representation
        # logger.debug('Raw status: \'%s\', previous status: %s'%(val, self.__v['status']))
        # Store previous status
        self.__v['laststatus'] = self.__v['status']
        # Convert daemon raw status to internal representation
        val = ('none' if val == '' else
               # Ignore index status
               'busy' if val == 'index' and self.__v['laststatus'] == "unknown" else
               self.__v['laststatus'] if val == 'index' and self.__v['laststatus'] != "unknown" else
               # Rename long error status
               'no_net' if val == 'no internet access' else
               # pass 'busy', 'idle' and 'paused' statuses 'as is'
               val if val in ['busy', 'idle', 'paused'] else
               # Status 'error' covers 'error', 'failed to connect to daemon process' and other.
               'error')
      elif key != 'progress' and val == '':   # 'progress' can be '' the rest - can't
        val = '...'                           # Make default filling for empty values
      # Check value change and store changed
      if self.__v[key] != val:                # Check change of value
        self.__v[key] = val                   # Store new value
        if key == 'status':
          self.__v['statchg'] = True          # Remember that status changed
        elif key == 'progress':
          self.__v['statchg'] = True          # Remember that progress changed
        else:
          self.__v['szchg'] = True            # Remember that something changed in sizes values
    # Parse last synchronized items
    buf = reFindall(r".*: '(.*)'\n", files)
    # Check if file list has been changed
    if self.__v['lastitems'] != buf:
      self.__v['lastitems'] = buf             # Store the new file list
      self.__v['lastchg'] = True              # Remember that it is changed
    # return True when something changed, if nothing changed - return False
    return self.__v['statchg'] or self.__v['szchg'] or self.__v['lastchg']

  def start(self):                      # Execute 'yandex-disk start'
    '''
    Execute 'yandex-disk start' 
    Additionally it starts watcher in case of success start
    '''
    def do_start():
      if self.getOutput() != "":
        logger.info('Daemon is already started')
        self.__watcher.start()    # Activate file watcher
        return
      try:                        # Try to start
        msg = check_output([self.__YDC, '-c', self.config.fileName, 'start'], universal_newlines=True)
        logger.info('Start success, message: %s' % msg)
      except CalledProcessError as e:
        logger.error('Daemon start failed:%s' % e.output)
        return
      self.__watcher.start()      # Activate file watcher
    Thread(None, do_start).start()

  def stop(self):                       # Execute 'yandex-disk stop'
    def do_stop():
      if self.getOutput() == "":
        logger.info('Daemon is not started')
        return
      try:
        msg = check_output([self.__YDC, '-c', self.config.fileName, 'stop'],
                          universal_newlines=True)
        logger.info('Start success, message: %s' % msg)
      except:
        logger.info('Start failed')
    Thread(None, do_stop).start()

  def exit(self):                       # Handle daemon/indicator closing
    logger.debug("Indicator %sexit started: " % self.ID)
    self.__watcher.stop()
    self.__timer.cancel()  # stop event timer if it is running
    # Stop yandex-disk daemon if it is required by its configuration
    if self.config.get('stoponexitfromindicator', False):
      self.stop()
      logger.info('Demon %sstopped' % self.ID)
    logger.debug('Indicator %sexited' % self.ID)
