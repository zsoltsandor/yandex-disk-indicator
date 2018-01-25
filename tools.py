#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from logging import getLogger
logger = getLogger('')

from gi import require_version
require_version('GLib', '2.0')
from gi.repository.GLib import timeout_add, source_remove

class Timer(object):            # Timer for triggering a function periodically
  ''' Timer class methods:
        __init__ - initialize the timer object with specified interval and handler. Start it
                   if start value is not False. par - is parameter for handler call.
        start    - Start timer. Optionally the new interval can be specified and if timer is
                   already running then the interval is updated (timer restarted with new interval).
        update   - Updates interval. If timer is running it is restarted with new interval. If it
                   is not running - then new interval is just stored.
        stop     - Stop running timer or do nothing if it is not running.
      Interface variables:
        active   - True when timer is currently running, otherwise - False
  '''
  def __init__(self, interval, handler, par=None, start=True):
    self.interval = interval          # Timer interval (ms)
    self.handler = handler            # Handler function
    self.par = par                    # Parameter of handler function
    self.active = False               # Current activity status
    if start:
      self.start()                    # Start timer if required

  def start(self, interval=None):   # Start inactive timer or update if it is active
    if interval is None:
      interval = self.interval
    if not self.active:
      self.interval = interval
      if self.par is None:
        self.timer = timeout_add(interval, self.handler)
      else:
        self.timer = timeout_add(interval, self.handler, self.par)
      self.active = True
      # logger.debug('timer started %s %s' %(self.timer, interval))
    else:
      self.update(interval)

  def update(self, interval):         # Update interval (restart active, not start if inactive)
    if interval != self.interval:
      self.interval = interval
      if self.active:
        self.stop()
        self.start()

  def stop(self):                     # Stop active timer
    if self.active:
      # logger.debug('timer to stop %s' %(self.timer))
      source_remove(self.timer)
      self.active = False


from pyinotify import ProcessEvent, WatchManager, Notifier, IN_MODIFY, IN_ACCESS

class iWatcher(object):                # File changes watcher
  '''
  Watcher class for monitor of changes in file.
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
    self._iNotifier = Notifier(self._watchMngr, _EH(), timeout=0.5)
    # Timer will call iNotifier handler
    def iNhandle():                    # iNotify working routine (called by timer)
      while self._iNotifier.check_events():
        self._iNotifier.read_events()
        self._iNotifier.process_events()
      return True
    self._timer = Timer(700, iNhandle, start=False)  # not started initially
    self._status = False

  def start(self):                    # Activate iNotify watching
    if self._status:
      return
    if not pathExists(self.path):
      logger.info("iNotiy was not started: path '"+self.path+"' was not found.")
      return
    self._watch = self._watchMngr.add_watch(self.path, IN_MODIFY|IN_ACCESS, rec=False)
    self._timer.start()
    self._status = True

  def stop(self):                     # Stop iNotify watching
    if not self._status:
      return
    # Remove watch
    self._watchMngr.rm_watch(self._watch[self.path])
    # Stop timer
    self._timer.stop()
    self._status = False


from os import stat
from os.path import exists as pathExists

class Watcher(object):                # File changes watcher
  '''
  Watcher class for monitor of changes in file.
  '''
  def __init__(self, path, handler, par=None):
    self.path = path
    self.par = par
    def wHandle():
      st = stat(self.path).st_ctime_ns
      if st != self.mark:
        self.mark = st
        handler(self.par)
      return True
    if not pathExists(self.path):
      logger.info("Watcher was not started: path '"+self.path+"' was not found.")
    else:
      self.mark = stat(self.path).st_ctime_ns
      
    self.timer = Timer(700, wHandle, start=False)  # not started initially
    self.status = False

  def start(self):                    # Activate iNotify watching
    if self.status:
      return
    if not pathExists(self.path):
      logger.info("Watcher was not started: path '"+self.path+"' was not found.")
      return
    self.mark = stat(self.path).st_ctime_ns
    self.timer.start()
    self.status = True

  def stop(self):                     # Stop watching
    if not self.status:
      return
    # Stop timer
    self.timer.stop()
    self.status = False
