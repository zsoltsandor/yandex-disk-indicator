#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from os import remove, makedirs, getpid, geteuid, getenv
from subprocess import check_output, CalledProcessError, call
from re import findall as reFindall, sub as reSub, search as reSearch, M as reM, S as reS
from argparse import ArgumentParser
from logging import getLogger
from os.path import exists as pathExists, join as pathJoin, relpath as relativePath, expanduser
from shutil import copy as fileCopy, which
from sys import exit as sysExit

from tools import *

logger = getLogger('')

#################### Common utility functions and classes ####################
def copyFile(src, dst):
  try:
    fileCopy(src, dst)
  except:
    logger.error("File Copy Error: from %s to %s" % (src, dst))

def deleteFile(dst):
  try:
    remove(dst)
  except:
    logger.error('File Deletion Error: %s' % dst)

def makeDirs(dst):
  try:
    makedirs(dst, exist_ok=True)
  except:
    logger.error('Dirs creation Error: %s' % dst)

def shortPath(path):
  return (path[: 20] + '...' + path[-27:] if len(path) > 50 else path).replace('_', '\u02CD')

class CVal(object):             # Multivalue helper
  ''' Class to work with value that can be None, scalar item or list of items depending
      of number of elementary items added to it or it contain. '''

  def __init__(self, initialValue=None):
    self.set(initialValue)   # store initial value
    self.index = None

  def get(self):                  # It just returns the current value of cVal
    return self.val

  def set(self, value):           # Set internal value
    self.val = value
    if isinstance(self.val, list) and len(self.val) == 1:
      self.val = self.val[0]
    return self.val

  def add(self, item):            # Add item
    if isinstance(self.val, list):  # Is it third, fourth ... value?
      self.val.append(item)         # Just append new item to list
    elif self.val is None:          # Is it first item?
      self.val = item               # Just store item
    else:                           # It is the second item.
      self.val = [self.val, item]   # Convert scalar value to list of items.
    return self.val

  def remove(self, item):         # remove item
    if isinstance(self.val, list):
      self.val.remove(item)
      if len(self.val) == 1:
        self.val = self.val[0]
    elif self.val is not None and self.val == item: 
      self.val = None
    return self.val

  def __len__(self):
    if isinstance(self.val, list):
      return len(self.val)
    elif self.val is None:        
      return 0
    else:
      return 1

  def __iter__(self):             # cVal iterator object initialization
    if isinstance(self.val, list):  # Is CVal a list?
      self.index = -1
    elif self.val is None:          # Is CVal not defined?
      self.index = None
    else:                           # CVal is scalar type.
      self.index = -2
    return self

  def __next__(self):             # cVal iterator support
    if self.index is None:            # Is CVal not defined?
      raise StopIteration             # Stop iterations
    self.index += 1
    if self.index >= 0:               # Is CVal a list?
      if self.index < len(self.val):  # Is there a next element in list?
        return self.val[self.index]
      else:                           # There is no more elements in list.
        self.index = None
        raise StopIteration           # Stop iterations
    else:                             # CVal has scalar type.
      self.index = None               # Remember that there is no more iterations possible
      return self.val

  def __bool__(self):
    return self.val is not None

class Config(dict):             # Configuration

  def __init__(self, fileName, load=True,
               bools=[['true', 'yes', 'y'], ['false', 'no', 'n']],
               boolval=['yes', 'no'], usequotes=True, delimiter='='):
    super().__init__()
    self.fileName = fileName
    self.bools = bools             # Values to detect boolean in self.load
    self.boolval = boolval         # Values to write boolean in self.save
    self.usequotes = usequotes     # Use quotes for keys and values in self.save
    self.delimiter = delimiter     # Use specified delimiter between key and value
    self.changed = False           # Change flag (for use outside of the class)
    if load:
      self.load()

  def decode(self, value):              # Convert string to value before store it
    if value.lower() in self.bools[0]:
      value = True
    elif value.lower() in self.bools[1]:
      value = False
    return value

  def getValue(self, st):               # Find value(s) in string after '='
    v = CVal()                                    # Value accumulator
    st = st.strip()                               # Remove starting and ending spaces
    if st.startswith(','):
      return None                                 # Error: String after '=' starts with comma
    while True:
      s = reSearch(r'^("[^"]*")|^([^",#]+)', st)  # Search for quoted or value without quotes
      if s is None:
        return None                               # Error: Nothing found but value expected
      start, end = s.span()
      vv = st[start: end].strip()                 # Get found value
      if vv.startswith('"'):
        vv = vv[1: -1]                            # Remove quotes
      v.add(self.decode(vv))                      # Decode and store value
      st = st[end:].lstrip()                      # Remove value and following spaces from string
      if st == '':
        return v.get()                            # EOF normaly reached (after last value in string)
      else:                                       # String still contain something
        if st.startswith(','):                    # String is continued with comma?
          st = st[1:].lstrip()                    # Remove comma and following spaces
          if st != '':                            # String is continued after comma?
            continue                              # Continue to search values
          # else:                                 # Error: No value after comma
        # else:                                   # Error: Next symbol is not comma
        return None                               # Error

  def load(self, bools=[['true', 'yes', 'y'], ['false', 'no', 'n']], delimiter='='):
    """
    Reads config file to dictionary.
    Config file should contain key=value rows.
    Key can be quoted or not.
    Value can be one item or list of comma-separated items. Each value item can be quoted or not.
    When value is a single item then it creates key:value item in dictionary
    When value is a list of items it creates key:[value, value,...] dictionary's item.
    """
    self.bools = bools
    self.delimiter = delimiter
    try:                              # Read configuration file into list of tuples ignoring blank
                                      # lines, lines without delimiter, and lines with comments.
      with open(self.fileName) as cf:
        res = [reFindall(r'^\s*(.+?)\s*%s\s*(.*)$' % self.delimiter, l)[0]
               for l in cf if l and self.delimiter in l and l.lstrip()[0] != '#']
      self.readSuccess = True
    except:
      logger.error('Config file read error: %s' % self.fileName)
      self.readSuccess = False
      return False
    for kv, vv in res:                # Parse each line
      # Check key
      key = reFindall(r'^"([^"]+)"$|^([\w-]+)$', kv)
      if key == []:
        logger.warning('Wrong key in line \'%s %s %s\'' % (kv, self.delimiter, vv))
      else:                           # Key is OK
        key = key[0][0] + key[0][1]   # Join two possible keys variants (with and without quotes)
        if vv.strip() == '':
          logger.warning('No value specified in line \'%s %s %s\'' % (kv, self.delimiter, vv))
        else:                         # Value is not empty
          value = self.getValue(vv)   # Parse values
          if value is None:
            logger.warning('Wrong value(s) in line \'%s %s %s\'' % (kv, self.delimiter, vv))
          else:                       # Value is OK
            if key in self.keys():    # Check double values
              logger.warning(('Double values for one key:\n%s = %s\nand\n%s = %s\n' +
                              'Last one is stored.') % (key, self[key], key, value))
            self[key] = value         # Store last value
            logger.debug('Config value read as: %s = %s' % (key, str(value)))
    logger.info('Config read: %s' % self.fileName)
    return True

  def encode(self, val):                # Convert value to string before save it
    if isinstance(val, bool):       # Treat Boolean
      val = self.boolval[0] if val else self.boolval[1]
    if self.usequotes:
      val = '"' + val + '"'         # Put value within quotes
    return val

  def save(self, boolval=['yes', 'no'], usequotes=True, delimiter='='):
    self.usequotes = usequotes
    self.boolval = boolval
    self.delimiter = delimiter
    try:                                  # Read the file in buffer
      with open(self.fileName, 'rt') as cf:
        buf = cf.read()
    except:
      logger.warning('Config file access error, a new file (%s) will be created' % self.fileName)
      buf = ''
    buf = reSub(r'[\n]*$', '\n', buf)     # Remove all ending blank lines except the one.
    for key, value in self.items():
      if value is None:
        res = ''                          # Remove 'key=value' from file if value is None
        logger.debug('Config value \'%s\' will be removed' % key)
      else:                               # Make a line with value
        res = self.delimiter.join([key,
                                   ', '.join([self.encode(val) for val in CVal(value)])]) + '\n'
        logger.debug('Config value to save: %s' % res[:-1])
      # Find line with key in file the buffer
      sRe = reSearch(r'^[ \t]*["]?%s["]?[ \t]*%s.+\n' % (key, self.delimiter), buf, flags=reM)
      if sRe is not None:                 # Value has been found
        buf = sRe.re.sub(res, buf)        # Replace it with new value
      elif res != '':                     # Value was not found and value is not empty
        buf += res                        # Add new value to end of file buffer
    try:
      with open(self.fileName, 'wt') as cf:
        cf.write(buf)                     # Write updated buffer to file
    except:
      logger.error('Config file write error: %s' % self.fileName)
      return False
    logger.info('Config written: %s' % self.fileName)
    self.changed = False                  # Reset flag of change in not stored config
    return True

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
             The parameters of the call - status values dictionary (see vars description below)

  Class interface variables:
  config   - The daemon configuration dictionary (object of _DConfig(Config) class)
  vars     - status values dictionary with following keys:
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
  ID       - the daemon identity string (empty in single daemon configuration)
  '''

  class _DConfig(Config):               # Redefined class for daemon config

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
    self.YDC = which('yandex-disk')
    if not self.YDC:
      sysExit(_('Yandex.Disk utility is not installed.\n ' +
            'Visit www.yandex.ru, download and install Yandex.Disk daemon.'))
    # Try to read Yandex.Disk configuration file and make sure that it is correctly configured
    self.config = self._DConfig(cfgFile, load=False)
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
    self._wTimer = Timer(500, self._eventHandler, par=False, start=True)
    self._tCnt = 0
    self._iNtfyWatcher = Watcher(pathJoin(
                                  self.config['dir'].replace('~', getenv("HOME")), 
                                  '.sync/cli.log'),
                                self._eventHandler, 
                                par=True)
    # Set initial daemon status values
    self.vals = {'status': 'unknown', 'progress': '', 'laststatus': 'unknown', 'statchg': True,
                 'total': '...', 'used': '...', 'free': '...', 'trash': '...', 'szchg': True,
                 'error':'', 'path':'', 'lastitems': [], 'lastchg': True}
    if self.config.get('startonstartofindicator', True):
      self.start()                      # Start daemon if it is required
    else:
      self._iNtfyWatcher.start()        # try to activate file watcher

  def errorDialog(self, _):
    # it is virtual method
    return 0

  def _eventHandler(self, iNtf):        # Daemon event handler
    '''
    Handle iNotify and and Timer based events.
    After receiving and parsing the daemon output it raises outside change event if daemon changes
    at least one of its status values.
    It can be called by timer (when iNtf=False) or by iNonifier (when iNtf=True)
    '''

    # Parse fresh daemon output. Parsing returns true when something changed
    if self._parseOutput(self.getOutput()):
      logger.debug(self.ID + 'Event raised by' + ('iNtfy ' if iNtf else 'Timer '))
      self.change(self.vals)                  # Raise outside update event
    # --- Handle timer delays ---
    if iNtf:                                  # True means that it is called by iNonifier
      self._wTimer.update(2000)               # Set timer interval to 2 sec.
      self._tCnt = 0                          # Reset counter as it was triggered not by timer
    else:                                     # It called by timer
      if self.vals['status'] != 'busy':       # In 'busy' keep update interval (2 sec.)
        if self._tCnt < 9:                    # Increase interval up to 10 sec (2 + 8)
          self._wTimer.update((2 + self._tCnt) * 1000)
          self._tCnt += 1                     # Increase counter to increase delay next activation.
    return True                               # True is required to continue activations by timer.

  def change(self, vals):               # Redefined update handler
    logger.debug('Update event: %s \nValues : %s' % (str(update), str(vals)))

  def getOutput(self, userLang=False):  # Get result of 'yandex-disk status'
    cmd = [self.YDC, '-c', self.config.fileName, 'status']
    if not userLang:      # Change locale settings when it required
      cmd = ['env', '-i', "LANG='en_US.UTF8'", "TMPDIR=%s"%self.tmpDir] + cmd
    try:
      output = check_output(cmd, universal_newlines=True)
    except:
      output = ''         # daemon is not running or bad
    # logger.debug('output = %s' % output)
    return output

  def _parseOutput(self, out):          # Parse the daemon output
    '''
    It parses the daemon output and check that something changed from last daemon status.
    The self.vals dictionary is updated with new daemon statuses and self.update set represents
    the changes in self.vals. It returns True is something changed

    Daemon status is converted form daemon raw statuses into internal representation.
    Internal status can be on of the following: 'busy', 'idle', 'paused', 'none', 'no_net', 'error'.
    Conversion is done by following rules:
     - empty status (daemon is not running) converted to 'none'
     - statuses 'busy', 'idle', 'paused' are passed 'as is'
     - 'index' is ignored (previous status is kept)
     - 'no internet access' converted to 'no_net'
     - 'error' covers all other errors, except 'no internet access'
    '''
    self.vals['statchg'] = False
    self.vals['szchg'] = False
    self.vals['lastchg'] = False
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
        # logger.debug('Raw status: \'%s\', previous status: %s'%(val, self.vals['status']))
        # Store previous status
        self.vals['laststatus'] = self.vals['status']
        # Convert daemon raw status to internal representation
        val = ('none' if val == '' else
               # Ignore index status
               'busy' if val == 'index' and self.vals['laststatus'] == "unknown" else
               self.vals['laststatus'] if val == 'index' and self.vals['laststatus'] != "unknown" else
               # Rename long error status
               'no_net' if val == 'no internet access' else
               # pass 'busy', 'idle' and 'paused' statuses 'as is'
               val if val in ['busy', 'idle', 'paused'] else
               # Status 'error' covers 'error', 'failed to connect to daemon process' and other.
               'error')
      elif key != 'progress' and val == '':   # 'progress' can be '' the rest - can't
        val = '...'                           # Make default filling for empty values
      # Check value change and store changed
      if self.vals[key] != val:               # Check change of value
        self.vals[key] = val                  # Store new value
        if key == 'status':
          self.vals['statchg'] = True         # Remember that status changed
        elif key == 'progress':
          self.vals['statchg'] = True         # Remember that progress changed
        else:
          self.vals['szchg'] = True           # Remember that something changed in sizes values
    # Parse last synchronized items
    buf = reFindall(r".*: '(.*)'\n", files)
    # Check if file list has been changed
    if self.vals['lastitems'] != buf:
      self.vals['lastitems'] = buf            # Store the new file list
      self.vals['lastchg'] = True             # Remember that it is changed
    # return True when something changed, if nothing changed - return False
    return self.vals['statchg'] or self.vals['szchg'] or self.vals['lastchg']

  def start(self):                      # Execute 'yandex-disk start'
    '''
    Execute 'yandex-disk start' and return '' if success or error message if not
    ... but sometime it starts successfully with error message
    Additionally it starts iNotify monitoring in case of success start
    '''
    if self.getOutput() != "":
      logger.info('Daemon is already started')
      self._iNtfyWatcher.start()    # Activate iNotify watcher
      return
    try:                                          # Try to start
      msg = check_output([self.YDC, '-c', self.config.fileName, 'start'], universal_newlines=True)
      logger.info('Start success, message: %s' % msg)
    except CalledProcessError as e:
      logger.error('Daemon start failed:%s' % e.output)
      return
    self._iNtfyWatcher.start()    # Activate iNotify watcher

  def stop(self):                       # Execute 'yandex-disk stop'
    if self.getOutput() == "":
      logger.info('Daemon is not started')
      return
    try:
      msg = check_output([self.YDC, '-c', self.config.fileName, 'stop'],
                         universal_newlines=True)
      logger.info('Start success, message: %s' % msg)
    except:
      logger.info('Start failed')

  def exit(self):                       # Handle daemon/indicator closing
    self._iNtfyWatcher.stop()
    # Stop yandex-disk daemon if it is required by its configuration
    if self.vals['status'] != 'none' and self.config.get('stoponexitfromindicator', False):
      self.stop()
      logger.info('Demon %sstopped' % self.ID)

def activateActions(activate, installDir):  # Install/deinstall file extensions
  userHome = getenv("HOME")
  result = False
  try:                  # Catch all exceptions during FM action activation/deactivation

    # --- Actions for Nautilus ---
    if which("nautilus") is not None:
      logger.info("Nautilus installed")
      ver = check_output(["lsb_release -r | sed -n '1{s/[^0-9]//g;p;q}'"], shell=True)
      if ver != '' and int(ver) < 1210:
        nautilusPath = ".gnome2/nautilus-scripts/"
      else:
        nautilusPath = ".local/share/nautilus/scripts"
      logger.debug(nautilusPath)
      if activate:      # Install actions for Nautilus

        copyFile(pathJoin(installDir, "fm-actions/Nautilus_Nemo/publish"),
                 pathJoin(userHome, nautilusPath, _("Publish via Yandex.Disk")))
        copyFile(pathJoin(installDir, "fm-actions/Nautilus_Nemo/unpublish"),
                 pathJoin(userHome, nautilusPath, _("Unpublish from Yandex.disk")))
      else:             # Remove actions for Nautilus
        deleteFile(pathJoin(userHome, nautilusPath, _("Publish via Yandex.Disk")))
        deleteFile(pathJoin(userHome, nautilusPath, _("Unpublish from Yandex.disk")))
      result = True

    # --- Actions for Nemo ---
    if which("nemo") is not None:
      logger.info("Nemo installed")
      if activate:      # Install actions for Nemo
        copyFile(pathJoin(installDir, "fm-actions/Nautilus_Nemo/publish"),
                 pathJoin(userHome, ".local/share/nemo/scripts", _("Publish via Yandex.Disk")))
        copyFile(pathJoin(installDir, "fm-actions/Nautilus_Nemo/unpublish"),
                 pathJoin(userHome, ".local/share/nemo/scripts", _("Unpublish from Yandex.disk")))
      else:             # Remove actions for Nemo
        deleteFile(pathJoin(userHome, ".gnome2/nemo-scripts", _("Publish via Yandex.Disk")))
        deleteFile(pathJoin(userHome, ".gnome2/nemo-scripts", _("Unpublish from Yandex.disk")))
      result = True

    # --- Actions for Thunar ---
    if which("thunar") is not None:
      logger.info("Thunar installed")
      ucaPath = pathJoin(userHome, ".config/Thunar/uca.xml")
      # Read uca.xml
      with open(ucaPath) as ucaf:
        [(ust, actions, uen)] = reFindall(r'(^.*<actions>)(.*)(<\/actions>)', ucaf.read(), reS)
      acts = reFindall(r'(<action>.*?<\/action>)', actions, reS)
      nActs = dict((reFindall(r'<name>(.+?)<\/name>', u, reS)[0], u) for u in acts)

      if activate:      # Install actions for Thunar
        if _("Publish via Yandex.Disk") not in nActs.keys():
          nActs[_("Publish via Yandex.Disk")] = ("<action><icon>folder-publicshare</icon>" +
                           '<name>' + _("Publish via Yandex.Disk") +
                           '</name><command>yandex-disk publish %f | xclip -filter -selection' +
                           ' clipboard; zenity --info ' +
                           '--window-icon=/usr/share/yd-tools/icons/yd-128.png ' +
                           '--title="Yandex.Disk" --ok-label="' + _('Close') + '" --text="' +
                           _('URL to file: %f was copied into clipboard.') +
                           '"</command><description/><patterns>*</patterns>' +
                           '<directories/><audio-files/><image-files/><other-files/>' +
                           "<text-files/><video-files/></action>")
        if _("Unpublish from Yandex.disk") not in nActs.keys():
          nActs[_("Unpublish from Yandex.disk")] = ("<action><icon>folder</icon><name>" +
                           _("Unpublish from Yandex.disk") +
                           '</name><command>zenity --info ' +
                           '--window-icon=/usr/share/yd-tools/icons/yd-128_g.png --ok-label="' +
                           _('Close') + '" --title="Yandex.Disk" --text="' +
                           _("Unpublish from Yandex.disk") +
                           ': `yandex-disk unpublish %f`"</command>' +
                           '<description/><patterns>*</patterns>' +
                           '<directories/><audio-files/><image-files/><other-files/>' +
                           "<text-files/><video-files/></action>")

      else:             # Remove actions for Thunar
        if _("Publish via Yandex.Disk") in nActs.keys():
          del nActs[_("Publish via Yandex.Disk")]
        if _("Unpublish from Yandex.disk") in nActs.keys():
          del nActs[_("Unpublish from Yandex.disk")]

      # Save uca.xml
      with open(ucaPath, 'wt') as ucaf:
        ucaf.write(ust + ''.join(u for u in nActs.values()) + uen)
      result = True

    # --- Actions for Dolphin ---
    if which("dolphin") is not None:
      logger.info("Dolphin installed")
      if activate:      # Install actions for Dolphin
        makeDirs(pathJoin(userHome, '.local/share/kservices5/ServiceMenus'))
        copyFile(pathJoin(installDir, "fm-actions/Dolphin/ydpublish.desktop"),
                 pathJoin(userHome, ".local/share/kservices5/ServiceMenus/ydpublish.desktop"))
      else:             # Remove actions for Dolphin
        deleteFile(pathJoin(userHome, ".local/share/kservices5/ServiceMenus/ydpublish.desktop"))
      result = True

    # --- Actions for Pantheon-files ---
    if which("pantheon-files") is not None:
      logger.info("Pantheon-files installed")
      ctrs_path = "/usr/share/contractor/"
      if activate:      # Install actions for Pantheon-files
        src_path = pathJoin(installDir, "fm-actions", "pantheon-files")
        ctr_pub = pathJoin(src_path, "yandex-disk-indicator-publish.contract")
        ctr_unpub = pathJoin(src_path, "yandex-disk-indicator-unpublish.contract")
        res = call(["gksudo", "-D", "yd-tools", "cp", ctr_pub, ctr_unpub, ctrs_path])
        if res == 0:
          result = True
        else:
          logger.error("Cannot enable actions for Pantheon-files")
      else:             # Remove actions for Pantheon-files
        res = call(["gksudo", "-D", "yd-tools", "rm",
                    pathJoin(ctrs_path, "yandex-disk-indicator-publish.contract"),
                    pathJoin(ctrs_path, "yandex-disk-indicator-unpublish.contract")])
        if res == 0:
          result = True
        else:
          logger.error("Cannot disable actions for Pantheon-files")

    # --- Actions for Caja ---
    if which("caja") is not None:
      logger.info("Caja installed")
      if activate:      # Install actions for Nemo
        copyFile(pathJoin(installDir, "fm-actions/Nautilus_Nemo/publish"),
                 pathJoin(userHome, ".config/caja/scripts", _("Publish via Yandex.Disk")))
        copyFile(pathJoin(installDir, "fm-actions/Nautilus_Nemo/unpublish"),
                 pathJoin(userHome, ".config/caja/scripts", _("Unpublish from Yandex.disk")))
      else:             # Remove actions for Nemo
        deleteFile(pathJoin(userHome, ".config/caja/scripts", _("Publish via Yandex.Disk")))
        deleteFile(pathJoin(userHome, ".config/caja/scripts", _("Unpublish from Yandex.disk")))
      result = True

  except Exception as e:
    logger.error("The following error occurred during the FM actions activation:\n %s" % str(e))
  return result

def argParse(appVer):           # Parse command line arguments
  parser = ArgumentParser(description=_('Desktop indicator for yandex-disk daemon'), add_help=False)
  group = parser.add_argument_group(_('Options'))
  group.add_argument('-l', '--log', type=int, choices=range(10, 60, 10), dest='level', default=30,
            help=_('Sets the logging level: ' +
                   '10 - to show all messages (DEBUG), ' +
                   '20 - to show all messages except debugging messages (INFO), ' +
                   '30 - to show all messages except debugging and info messages (WARNING), ' +
                   '40 - to show only error and critical messages (ERROR), ' +
                   '50 - to show critical messages only (CRITICAL). Default: 30'))
  group.add_argument('-c', '--config', dest='cfg', metavar='path', default='',
            help=_('Path to configuration file of YandexDisk daemon. ' +
                   'This daemon will be added to daemons list' +
                   ' if it is not in the current configuration.' +
                   'Default: \'\''))
  group.add_argument('-r', '--remove', dest='rcfg', metavar='path', default='',
            help=_('Path to configuration file of daemon that should be removed' +
                   ' from daemos list. Default: \'\''))
  group.add_argument('-h', '--help', action='help', help=_('Show this help message and exit'))
  group.add_argument('-v', '--version', action='version', version='%(prog)s v.' + appVer,
            help=_('Print version and exit'))
  return parser.parse_args()

def checkAutoStart(path):       # Check that auto-start is enabled
  if pathExists(path):
    i = 1 if getenv('XDG_CURRENT_DESKTOP') in ('Unity', 'Pantheon') else 0
    with open(path, 'rt') as f:
      attr = reFindall(r'\nHidden=(.+)|\nX-GNOME-Autostart-enabled=(.+)', f.read())
      if attr:
        if attr[0][i] and attr[0][i] == ('true' if i else 'false'):
          return True
      else:
        return True
  return False

def setProcName(newname):
  from ctypes import cdll, byref, create_string_buffer
  libc = cdll.LoadLibrary('libc.so.6')
  buff = create_string_buffer(len(newname) + 1)
  buff.value = bytes(newname, 'UTF8')
  libc.prctl(15, byref(buff), 0, 0, 0)
