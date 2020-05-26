#coding=utf-8

from __future__ import absolute_import

from octoprint.util import RepeatedTimer
from octoprint.printer import UnknownScript

import serial			# we need this for the serial communcation with the SMuFF
import os, fnmatch
import re
import octoprint.plugin
import time
import sys

AT_SMUFF 	= "@SMuFF"
M115	 	= "M115"
M119	 	= "M119"
M280	 	= "M280 P"
M18			= "M18"
TOOL 		= "T"
NOTOOL		= "T255"
G1_E	 	= "G1 E"
ALIGN 	 	= "ALIGN"
REPEAT 		= "REPEAT"
LOAD 		= "LOAD"
SERVO		= "SERVO"
MOTORS		= "MOTORS"
ALIGN_SPEED	= " F"
ESTOP_TRG 	= "triggered"


class SmuffPlugin(octoprint.plugin.SettingsPlugin,
                  octoprint.plugin.AssetPlugin,
                  octoprint.plugin.TemplatePlugin,
				  octoprint.plugin.StartupPlugin):

	def __init__(self):
		self._fw_info 	= "?"
		self._cur_tool 	= "?"
		self._pre_tool 	= "?"
		self._endstops	= "?"
		self._skip_timer= False
		self._selector 	= False
		self._revolver 	= False
		self._feeder 	= False
		self._feeder2	= False
		self._no_log	= False
		self._is_aligned = False

	##~~ StartupPlugin mixin

	def on_timer_event(self):
		# poll tool active and endstop states periodically
		if self._skip_timer == False:
			self._no_log = True
			self.get_tool()
			self.get_endstops()
			self._no_log = False
		
		self._plugin_manager.send_plugin_message(self._identifier, {'type': 'status', 'tool': self._cur_tool, 'feeder': self._feeder, 'feeder2': self._feeder2 })

	def on_after_startup(self):
		# set up a timer to poll the SMuFF
		self._timer = RepeatedTimer(1.0, self.on_timer_event)
		self._timer.start()

	##~~ SettingsPlugin mixin

	def get_settings_defaults(self):
		self._logger.info("SMuFF plugin loaded, getting defaults")

		params = dict(
			firmware_info	= "No data. Please check connection!",
			baudrate		= __ser_baud__,
			tty 			= "Not found. Please enable the UART on your Raspi!",
			tool			= self._cur_tool,
			selector_end	= self._selector,
			revolver_end	= self._revolver,
			feeder_end		= self._feeder,
			feeder2_end		= self._feeder
		)

		__ser0__.timeout = 1

		# request firmware info from SMuFF 
		self._fw_info = self.send_and_wait(M115)
		if self._fw_info:
			params['firmware_info'] = self._fw_info
		
		# request the currently active tool
		if self.get_tool() == True:
			params['tool'] = self._cur_tool

		# request the endstop states
		if self.get_endstops() == True:
			params['selector_end'] = self._selector
			params['revolver_end'] = self._revolver
			params['feeder_end']   = self._feeder
			params['feeder2_end']  = self._feeder2

		# look up the serial port driver
		drvr = self.find_file(__ser_drvr__, "/dev")
		if len(drvr) > 0:
			params['tty'] = "Found! (/dev/" + __ser_drvr__ +")"

		return  params


		def get_template_configs(self):
			self._logger.info("Settings-Template was requested")
			return [
				dict(type="settings", custom_bindings=True, template='SMuFF_settings.jinja2')
			]

	##~~ AssetPlugin mixin

	def get_assets(self):
		# Define your plugin's asset files to automatically include in the
		# core UI here.
		return dict(
			js=["js/SMuFF.js"],
			css=["css/SMuFF.css"],
			less=["less/SMuFF.less"]
		)

	##~~ Softwareupdate hook

	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
		# for details.
		return dict(
			SMuFF=dict(
				displayName="SMuFF Plugin",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="technik-gegg",
				repo="OctoPrint-Smuff",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/technik-gegg/OctoPrint-Smuff/archive/{target_version}.zip"
			)
		)

	##~~ GCode hooks

	def extend_tool_queuing(self, comm_instance, phase, cmd, cmd_type, gcode, subcode, tags, *args, **kwargs):
		
		# self._logger.info("Processing queuing: [" + cmd + "," + str(cmd_type)+ "," + str(tags) + "]")
		
		if gcode and gcode.startswith(TOOL):

			# if the tool that's already loaded is addressed, ignore the filament change
			if cmd == self._cur_tool:
				self._logger.info(cmd + " equals " + self._cur_tool + " -- aborting tool change")
				return None
			self._is_aligned = False
			# replace the tool change command
			return [ AT_SMUFF + " " + cmd ]

		if cmd and cmd.startswith(AT_SMUFF):
			v1 = None
			v2 = None
			spd = 300
			action = None
			tmp = cmd.split()
			if len(tmp):
				action = tmp[1]
				if len(tmp) > 2:
					v1 = int(tmp[2])
				if len(tmp) > 3:
					v2 = int(tmp[3])
				if len(tmp) > 4:
					spd = int(tmp[4])

			self._logger.info(">> " + cmd + "  action: " + str(action) + "  v1,v2: " + str(v1) + ", " + str(v2))

			# @SMuFF T0...99
			if action and action.startswith(TOOL):
				if self._printer.set_job_on_hold(True):
					try:
						self._logger.info("Tx: Feeder: " + str(self._feeder) + ", Pending: " + str(self._pending_tool) + ", Current: " + str(self._cur_tool))
						self._pending_tool = action
						# check if there's some filament loaded
						if self._feeder and not self._cur_tool == NOTOOL:
							# send the "Before Tool Change" script to the printer
							self._logger.info("calling script")
							self._printer.script("beforeToolChange")
						else:
							self._logger.info("calling SMuFF LOAD")
							self._printer.commands(AT_SMUFF + " " + LOAD)

					except UnknownScript:
						self._logger.info("Script 'beforeToolChange' not found!")
						self._printer.set_job_on_hold(False)

			# @SMuFF LOAD
			if action and action == LOAD:
				try:
					self._logger.info("LOAD: Feeder: " + str(self._feeder) + ", Pending: " + str(self._pending_tool) + ", Current: " + str(self._cur_tool))
					self._skip_timer = True
					# send a tool change command to SMuFF
					stat = self.send_and_wait(self._pending_tool)
					self._skip_timer = False

					if stat != None:
						self._pre_tool = self._cur_tool
						self._cur_tool = self._pending_tool
					# send the "After Tool Change" script to the printer
					self._printer.script("afterToolChange")

				except UnknownScript:
					self._logger.info("Script 'afterToolChange' not found!")
				
				finally:
					self._printer.set_job_on_hold(False)
			
				return ""
			
			# @SMuFF SERVO
			if action and action == SERVO:
				self._skip_timer = True
				# send a servo command to SMuFF
				self.send_and_wait(M280 + str(v1) + " S" + str(v2))
				self._skip_timer = False
				return ""

			# @SMuFF MOTORS
			if action and action == MOTORS:
				self._skip_timer = True
				# send a servo command to SMuFF
				self.send_and_wait(M18)
				self._skip_timer = False
				return ""

			# @SMuFF ALIGN | REPEAT
			if action and action == ALIGN or action == REPEAT:
				if self._is_aligned:
					return ""
				# check the feeder and keep retracting v1 as long as 
				# the feeder endstop is on
				self.get_endstops()
				self._logger.info(action + " Feeder is: " + str(self._feeder) + " Cmd is:" + G1_E + str(v1))
				if self._feeder:
					self._is_aligned = False
					return [ ( G1_E + str(v1) + ALIGN_SPEED + str(spd) ) ]
				else:
					self._is_aligned = True
					self._logger.info("Now aligned, cmd is: " + G1_E + str(v2))
					# finally retract from selector (distance = v2)
					return [ ( G1_E + str(v2) + ALIGN_SPEED + str(spd) ) ]


	def extend_tool_sending(self, comm_instance, phase, cmd, cmd_type, gcode, subcode, tags, *args, **kwargs):

		if gcode and gcode.startswith(TOOL):
			return ""

		# is this the replaced tool change command?
		if cmd and cmd.startswith(AT_SMUFF):
			v1 = None
			v2 = None
			action = None
			tmp = cmd.split()
			if len(tmp):
				action = tmp[1]
				if len(tmp) > 2:
					v1 = int(tmp[2])
				if len(tmp) > 3:
					v2 = int(tmp[3])

			self._logger.info(">>> " + cmd + "  action: " + str(action) + "  v1,v2: " + str(v1) + ", " + str(v2))

		

	def extend_script_variables(comm_instance, script_type, script_name, *args, **kwargs):
		if script_type and script_type == "gcode":
			variables = dict(
				feeder	= self._feeder,
				feeder2	= self._feeder2,
				tool	= self._cur_tool
			)
			return None, None, variables
		return None
	
	##~~ helper functions

	def send_and_wait(self, data):
		if __ser0__.is_open:
			__ser0__.write("{}\n".format(data))
			__ser0__.flush()
			prev_resp = ""
			retry = 15 	# wait max. 15 seconds for response
			while True:
				try:
					response = __ser0__.readline()
					if response.startswith('echo:'):
						continue
					elif response.startswith('ok\n'):
						return prev_resp
					else:
						prev_resp = response.rstrip("\n")
						if prev_resp:
							if self._no_log == False:
								self._logger.info("SMuFF says [" + prev_resp + "] to [" + data +"]")
						retry -= 1
						if retry == 0:
							return None

				except (OSError, serial.SerialException):
					self._logger.info("Serial Exception!")
					continue
		else:
			self._logger.info("Serial not open")
			return None


	def find_file(self, pattern, path):
		result = []
		for root, dirs, files in os.walk(path):
			for name in files:
				if fnmatch.fnmatch(name, pattern):
					result.append(os.path.join(root, name))
		return result


	def get_tool(self):
		self._cur_tool = self.send_and_wait(TOOL)
		if self._cur_tool:
			return True
		return False


	def get_endstops(self):
		self._endstops = self.send_and_wait(M119)
		if self._endstops:
			self.parse_endstop_states(self._endstops)
			return True
		return False


	def parse_tool_number(self, tool):
		result = -1
		
		if len(tool) == 0:
			return result

		try:
			result = int(re.search(r'\d+', tool).group(0))
		except ValueError:
			self._logger.into("Can't parse tool number: [" + tool + "]")
		except:
			self._logger.info("Can't parse tool number: [Unexpected error]")
		return result


	def parse_endstop_states(self, states):
		#self._logger.info("Endstop states: [" + states + "]")
		if len(states) == 0:
			return False
		m = re.search(r'^(\w+:.)(\w+).(\w+:.)(\w+).(\w+:.)(\w+)', states)
		if m:
			self._selector = m.group(2).strip() == ESTOP_TRG
			self._revolver = m.group(4).strip() == ESTOP_TRG
			self._feeder 	 = m.group(6).strip() == ESTOP_TRG
			self._feeder2  = False # m.group(8).strip() == ESTOP_TRG
			# self._logger.info("FEEDER: [" + str(self._feeder) +"]")
			return True
		return False
		

__plugin_name__ = "SMuFF Plugin"

def __plugin_load__():
	global __plugin_implementation__
	global __plugin_hooks__
	global __ser0__
	global __ser_drvr__
	global __ser_baud__

	__plugin_implementation__ = SmuffPlugin()

	__plugin_hooks__ = {
		"octoprint.comm.protocol.scripts": 				__plugin_implementation__.extend_script_variables,
    	"octoprint.comm.protocol.gcode.sending": 		__plugin_implementation__.extend_tool_sending,
    	"octoprint.comm.protocol.gcode.queuing": 		__plugin_implementation__.extend_tool_queuing,
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}

	# change the baudrate here if you have to
	__ser_baud__ = 115200
	# do __not__ change the serial port device
	__ser_drvr__ = "ttyS0"

	try:
		__ser0__ = serial.Serial("/dev/"+__ser_drvr__, __ser_baud__, timeout=1)
		# after connecting, read the response from the SMuFF
		resp = __ser0__.readline()
		# which is supposed to be 'start'
		if resp.startswith('start'):
			self._logger.info("SMuFF has sent \"start\" response")

	except (OSError, serial.SerialException):
		self._logger.info("Serial port not found!")
		#pass




def __plugin_unload__():
	try:
		if __ser0__.is_open:
			__ser0__.close()
	except (OSError, serial.SerialException):
		pass


def __plugin_disabled():
	try:
		if __ser0__.is_open:
			__ser0__.close()
	except (OSError, serial.SerialException):
		pass
