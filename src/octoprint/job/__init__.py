from __future__ import absolute_import, unicode_literals


import logging
import os
import time

from octoprint.comm.protocol import FileAwareProtocolListener

from abc import ABCMeta, abstractmethod, abstractproperty

class Printjob(object):
	__metaclass__ = ABCMeta

	def __init__(self):
		self._logger = logging.getLogger(__name__)
		self._start = None
		self._protocol = None
		self._printer_profile = None

		self._listeners = []

	def register_listener(self, listener):
		self._listeners.append(listener)

	def unregister_listener(self, listener):
		self._listeners.remove(listener)

	def can_process(self, protocol):
		return False

	def process(self, protocol, position=0):
		self._start = time.time()
		self._protocol = protocol

	def cancel(self):
		pass

	def get_next(self):
		return None

	def get_time_estimate(self, lost_time=0):
		if self._start is None:
			return None

		progress = self.get_progress()
		if not progress:
			return None

		spent_time = time.time() - self._start - lost_time
		return spent_time / progress

	def get_progress(self):
		return 0.0

	def can_get_content(self):
		return False

	def get_content_generator(self):
		return None

	def process_job_started(self):
		self.notify_listeners("on_job_started", self)

	def process_job_done(self):
		self.notify_listeners("on_job_done", self)

	def process_job_cancelled(self):
		self.notify_listeners("on_job_cancelled", self)

	def process_job_failed(self):
		self.notify_listeners("on_job_failed")

	def notify_listeners(self, name, *args, **kwargs):
		for listener in self._listeners:
			method = getattr(listener, name, None)
			if not method:
				continue

			try:
				method(*args, **kwargs)
			except:
				self._logger.exception("Exception while calling {} on printjob listener {}".format(
					"{}({})".format(name, ", ".join(list(args) + ["{}={}".format(key, value)
					                                              for key, value in kwargs.items()]))
				))


class LocalFilePrintjob(Printjob):

	def __init__(self, path, encoding="utf-8"):
		Printjob.__init__(self)

		self._path = path
		self._encoding = encoding
		self._size = os.stat(path).st_size

		self._handle = None

	def process(self, protocol, position=0):
		Printjob.process(self, protocol, position=position)

		from octoprint.util import bom_aware_open
		self._handle = bom_aware_open(self._path, encoding=self._encoding, errors="replace")

		if position > 0:
			self._handle.seek(position)

	def get_next(self):
		from octoprint.util import to_unicode

		if self._handle is None:
			raise ValueError("File {} is not open for reading" % self._path)

		try:
			processed = None
			while processed is None:
				if self._handle is None:
					# file got closed just now
					return None
				line = to_unicode(self._handle.readline())
				if not line:
					self.notify_listeners("on_job_done", self)
					self.close()
				processed = self.process_line(line)

			return processed
		except Exception as e:
			self.close()
			self._logger.exception("Exception while processing line")
			raise e

	def process_line(self, line):
		return line

	def get_progress(self):
		if self._handle is None:
			return 0.0
		return self._handle.tell() / self._size

	def close(self):
		if self._handle is not None:
			try:
				self._handle.close()
			except:
				pass
		self._handle = None

	def can_get_content(self):
		return True

	def get_content_generator(self):
		from octoprint.util import bom_aware_open
		with bom_aware_open(self._path, encoding=self._encoding, error="replace") as f:
			for line in f.readline():
				yield line


class LocalGcodeFilePrintjob(LocalFilePrintjob):

	def can_process(self, protocol):
		return LocalGcodeFilePrintjob in protocol.supported_jobs

	def process_line(self, line):
		processed = line
		# strip comments

		# apply offsets

		# return result
		return processed


class LocalGcodeStreamjob(LocalGcodeFilePrintjob):

	def can_process(self, protocol):
		from octoprint.comm.protocol import FileStreamingProtocolMixin
		return LocalGcodeStreamjob in protocol.supported_jobs and isinstance(protocol, FileStreamingProtocolMixin)

	def process_line(self, line):
		# we do not change anything for sd file streaming
		return line


class SDFilePrintjob(Printjob, FileAwareProtocolListener):

	def __init__(self, filename, status_interval=2.0):
		Printjob.__init__(self)
		self._filename = filename
		self._status_interval = status_interval

		self._status_timer = None
		self._active = False

		self._size = None
		self._last_pos = None

	def can_process(self, protocol):
		from octoprint.comm.protocol import FileAwareProtocolMixin
		return SDFilePrintjob in protocol.supported_jobs and isinstance(protocol, FileAwareProtocolMixin)

	def process(self, protocol, position=0):
		Printjob.process(self, protocol, position=position)

		self._protocol.register_listener(self)
		self._protocol.start_file_print(self._filename, position=position)
		self._active = True
		self._last_pos = position

		from octoprint.util import RepeatedTimer
		self._status_timer = RepeatedTimer(self._status_interval, self._query_status, condition=self._query_active)
		self._status_timer.start()

	def get_progress(self):
		if self._size is None or self._last_pos is None:
			return None
		return self._last_pos / self._size

	def on_protocol_sd_file_list(self, files):
		pass

	def on_protocol_sd_status(self, name, pos, total):
		if name != self._filename:
			return
		self._last_pos = pos

	def on_protocol_file_print_started(self, name, size):
		if name != self._filename:
			return
		self._size = size

	def on_protocol_file_print_done(self):
		self._active = False
		self._protocol.unregister_listener(self)
		self.notify_listeners("on_job_done", self)

	def _query_status(self):
		self._protocol.get_file_print_status()

	def _query_active(self):
		return self._active


class PrintjobListener(object):

	def on_job_started(self, job):
		pass

	def on_job_done(self, job):
		pass

	def on_job_cancelled(self, job):
		pass

	def on_job_failed(self, job):
		pass
