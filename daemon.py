#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from tools import *

from pyinotify import ProcessEvent, WatchManager, ThreadedNotifier, IN_MODIFY, IN_ACCESS
from threading import Timer as thTimer, Lock, Thread

from logging import getLogger
logger = getLogger('')

#################### Main daemon class ####################
class YDDaemon(object):         # Yandex.Disk daemon interface
  '''
  This is the fully automated class that serves as daemon interface.
  Public methods:
  __init__ - Handles initialization of the object and as a part - auto-start daemon if it
             is required by configuration settings.
  output   - Provides daemon output (in user language) through the parameter of callback. Executed in separate thread 
  start    - Request to start daemon. Do nothing if it is alreday started. Executed in separate thread
  stop     - Request to stop daemon. Do nothing if it is not started. Executed in separate thread
  exit     - Handles 'Stop on exit' facility according to daemon configuration settings.
  change   - Virtual method for handling daemon status changes. It have to be redefined by UI class.
             The parameters of the call - status values dictionary with following keys:
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
  error    - Virtual method for error handling. It have to be redefined by UI class.
 
  Class interface variables:
  ID       - the daemon identity string (empty in single daemon configuration)
  config   - The daemon configuration dictionary (object of _DConfig(Config) class)
  '''
  #################### Virtual methods ##################
  # they have to be implemented in GUI part of code
  
  def error(self, err):                    # Error handler
    logger.debug(err)
    return 0 

  def change(self, vals):                  # Update handler
    logger.debug('Update event: %s \nValues : %s' % (str(update), str(vals)))

  #################### Private classes ####################
  class __Watcher(object):                 # File changes watcher implementation
    '''
    iNotify watcher object for monitor of changes daemon internal log for the fastest
    reaction on status change.
    '''
    def __init__(self, path, handler, par=None):
      # Watched path
      self.path = path
      # Initialize iNotify watcher
      class EH(ProcessEvent):            # Event handler class for iNotifier
        def process_IN_MODIFY(self, event):
          handler(par)
      self.watchMngr = WatchManager()    # Create watch manager
      # Create PyiNotifier
      self.iNotifier = ThreadedNotifier(self.watchMngr, EH(), timeout=500)
      self.iNotifier.start()
      self.status = False

    def start(self):               # Activate iNotify watching
      if self.status:
        return
      if not pathExists(self.path):
        logger.info("iNotiy was not started: path '"+self.path+"' was not found.")
        return
      self.watch = self.watchMngr.add_watch(self.path, IN_MODIFY|IN_ACCESS, rec=False)
      self.status = True

    def stop(self):                # Stop iNotify watching
      if not self.status:
        return
      # Remove watch
      self.watchMngr.rm_watch(self.watch[self.path])
      # Stop timer
      self.iNotifier.stop()
      self.status = False

  class __DConfig(Config):                 # Redefined class for daemon config

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

  #################### Private methods ####################
  def __init__(self, cfgFile, ID):         # Check that daemon installed and configured and initialize object
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
               pathExists(expanduser(self.config.get('dir', ''))) and
               pathExists(expanduser(self.config.get('auth', '')))):
      if self.errorDialog(cfgFile) != 0:
        if ID != '':
          self.config['dir'] = ''
          break   # Exit from loop in multi-instance configuration
        else:
          sysExit('Daemon is not configured')
    self.tmpDir = getenv("TMPDIR")
    if self.tmpDir is None:
        self.tmpDir = '/tmp'
    # Set initial daemon status values
    self.__v = {'status': 'unknown', 'progress': '', 'laststatus': 'unknown', 'statchg': True,
                'total': '...', 'used': '...', 'free': '...', 'trash': '...', 'szchg': True,
                'error':'', 'path':'', 'lastitems': [], 'lastchg': True}
    # Declare event handler staff for callback from watcher and timer
    self.__tCnt = 0                          # Timer event counter 
    self.__lock = Lock()                     # event handler lock 
    def eventHandler(watch):
      '''
      Handles watcher (when watch=False) and and timer (when watch=True) events.
      After receiving and parsing the daemon output it raises outside change event if daemon changes
      at least one of its status values.
      '''
      # Enter to critical section through acquiring of the lock as it can be called from two different threads
      self.__lock.acquire()
      # Parse fresh daemon output. Parsing returns true when something changed
      if self.__parseOutput(self.__getOutput()):
        logger.debug(self.ID + 'Event raised by' + (' Watcher' if watch else ' Timer'))
        self.change(self.__v)                # Call the callback of update event handler 
      # --- Handle timer delays ---
      self.__timer.cancel()                  # Cancel timer if it still active
      if watch or self.__v['status'] == 'busy':
        delay = 2                            # Initial delay
        self.__tCnt = 0                      # Reset counter 
      else:                                  # It called by timer
        delay = 2 + self.__tCnt              # Increase interval up to 10 sec (2 + 8)
        self.__tCnt += 1                     # Increase counter to increase delay next activation.
      if self.__tCnt < 9:                  
        self.__timer = thTimer(delay, eventHandler, (False,))
        self.__timer.start()
      # Leave the critical section
      self.__lock.release()
    
    # Initialize watcher staff
    self.__watcher = self.__Watcher(pathJoin(expanduser(self.config['dir']), '.sync/cli.log'), 
                               eventHandler, par=True)
    # Initialize timer staff
    self.__timer = thTimer(0.3, eventHandler, (False,))
    self.__timer.start()

    # Start daemon if it is required in configuration
    if self.config.get('startonstartofindicator', True):
      self.start()                       
    else:
      self.__watcher.start()             # try to activate file watcher

  def __getOutput(self, userLang=False):   # Get result of 'yandex-disk status'
    cmd = [self.__YDC, '-c', self.config.fileName, 'status']
    if not userLang:      # Change locale settings when it required
      cmd = ['env', '-i', "LANG='en_US.UTF8'", "TMPDIR=%s"%self.tmpDir] + cmd
    try:
      output = check_output(cmd, universal_newlines=True)
    except:
      output = ''         # daemon is not running or bad
    # logger.debug('output = %s' % output)
    return output

  def __parseOutput(self, out):            # Parse the daemon output
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

  #################### Interface methods ####################
  def output(self, callBack):              # Receive daemon output in separate thread and pass it back through the callback
    Thread(target=lambda:callBack(self.__getOutput(True))).start()

  def start(self, wait=False):             # Execute 'yandex-disk start' in separate thread
    '''
    Execute 'yandex-disk start' in separate thread
    Additionally it starts watcher in case of success start
    '''
    def do_start():
      if self.__getOutput() != "":
        logger.info('Daemon is already started')
        self.__watcher.start()    # Activate file watcher
        return
      try:                        # Try to start
        msg = check_output([self.__YDC, '-c', self.config.fileName, 'start'], universal_newlines=True)
        logger.info('Daemon started, message: %s' % msg)
      except CalledProcessError as e:
        logger.error('Daemon start failed:%s' % e.output)
        return
      self.__watcher.start()      # Activate file watcher
    t = Thread(target=do_start)
    t.start()
    if wait:
      t.join()

  def stop(self, wait=False):              # Execute 'yandex-disk stop' in separate thread
    def do_stop():
      if self.__getOutput() == "":
        logger.info('Daemon is not started')
        return
      try:
        msg = check_output([self.__YDC, '-c', self.config.fileName, 'stop'],
                          universal_newlines=True)
        logger.info('Daemon stopped, message: %s' % msg)
      except:
        logger.info('Daemon stop failed')
    t = Thread(target=do_stop)
    t.start()
    if wait:
      t.join()

  def exit(self):                          # Handle daemon/indicator closing
    logger.debug("Indicator %sexit started: " % self.ID)
    self.__watcher.stop()
    self.__timer.cancel()  # stop event timer if it is running
    # Stop yandex-disk daemon if it is required by its configuration
    if self.config.get('stoponexitfromindicator', False):
      self.stop(wait=True)
      logger.info('Demon %sstopped' % self.ID)
    logger.debug('Indicator %sexited' % self.ID)
