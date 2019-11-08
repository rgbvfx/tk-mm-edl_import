# -*- coding: utf-8 -*-
# Mind Machine customized

from edl import Parser
import re
import sgtk
import os
import sys
import threading

# by importing QT from sgtk rather than directly, we ensure that
# the code will be compatible with both PySide and PyQt.
from sgtk.platform.qt import QtCore, QtGui
from .ui.dialog import Ui_Dialog

# standard toolkit logger
logger = sgtk.platform.get_logger(__name__)

# regex to match shot comments that begin with *space dash space*
rgx_comment = '^\s*-\s*'


def show_dialog(app_instance):
    """
    Show the main dialog window.
    """
    # in order to handle UIs seamlessly, each toolkit engine has methods for launching
    # different types of windows. By using these methods, your windows will be correctly
    # decorated and handled in a consistent fashion by the system. 
    
    # we pass the dialog class to this method and leave the actual construction
    # to be carried out by toolkit.
    app_instance.engine.show_dialog("EDL Import", app_instance, AppDialog)


class AppDialog(QtGui.QWidget):
    """
    Main application dialog window
    """
    
    def __init__(self):
        """
        Constructor
        """
        logger.info("Launching EDL Import...")

        # first, call the base class and let it do its thing.
        QtGui.QWidget.__init__(self)
        
        # now load in the UI that was created in the UI designer
        self.ui = Ui_Dialog() 
        self.ui.setupUi(self)

        # hide
        self.ui.button_shotgun_import.hide()
        self.ui.context.hide()
        self.ui.progress_bar.hide()
        self.ui.progress_bar.setValue(0)

        # most of the useful accessors are available through the Application class instance
        # it is often handy to keep a reference to this. You can get it via the following method:
        # via the self._app handle we can for example access:
        # - The engine, via self._app.engine
        # - A Shotgun API instance, via self._app.shotgun
        # - An Sgtk API instance, via self._app.sgtk
        self._app = sgtk.platform.current_bundle()
        self.ui.context.setText("Current Context: {}".format(self._app.context))

        # connect buttons
        self.ui.button_file_open.clicked.connect(self._select_edl_file)
        self.ui.button_shotgun_import.clicked.connect(self._shotgun_import)

        # data
        self.edl_data = None
        self.element_list = list()
        self.first_time = True
        self.fps = '23.976'
        self.header_list = self.get_headers()
        self.last_edl_file_path = None
        self.output_file_name = None
        self.project = self._app.context.project
        self.project_name = self.project['name']
        self.user = self._app.context.user
        self.user_first_name = 'User'
        if self.user:
            self.user_first_name = self.user['name'].split()[0]

        # regex to match groups in clip name
        self.rgx_clip_name = re.compile('^RBW_([A-Z]{3}[0-9]{4})(\S*)(.*)$')

        # sg connection
        self.sg = self._app.shotgun

        # thread placeholder
        self._thread = None

        if 'name' in self.user:
            msg = 'Hi {}!'.format(self.user['name'])
            self.ui.label_status.setText(unicode(msg))

        msg = 'EDL Import app initialize by {}'.format(self.user['name'])
        logger.info(msg)

    def _create_table(self):

        # set initial rows and columns
        row_count = len(self.edl_data) + 1
        column_count = len(self.header_list)
        self.ui.table.setRowCount(row_count)
        self.ui.table.setColumnCount(column_count)

        black = QtGui.QColor(0, 0, 0)

        logger.info('Creating table header row')

        # create table header row
        col_num = 0
        for header_name in self.header_list:
            item = QtGui.QTableWidgetItem()
            item.setText(header_name)
            item.setBackground(black)
            item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)  # not ItemIsEditable
            self.ui.table.setItem(0, col_num, item)
            col_num += 1

        logger.info('Adding EDL data to table')

        # add all rows of edl data to the table
        for row_num, shot_dict in enumerate(self.edl_data):
            row_num += 1  # adjust for header row
            col_num = 0
            # use header names as keys
            for k in self.header_list:
                item = QtGui.QTableWidgetItem()
                if k == 'Import':
                    checkbox = QtGui.QCheckBox()
                    checkbox.setCheckState(QtCore.Qt.Checked)
                    self.ui.table.setCellWidget(row_num, col_num, checkbox)
                else:
                    item = QtGui.QTableWidgetItem()
                    if k == 'Entity Type':
                        item.setText('Element')
                    else:
                        item.setText(shot_dict[k])
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)  # not ItemIsEditable
                self.ui.table.setItem(row_num, col_num, item)
                col_num += 1

        # create list of parents shots in table
        parent_shot_list = list()
        parent_shots_column = self.header_list.index('Parent Shots')
        for row in range(1, self.ui.table.rowCount()+1):
            item = self.ui.table.item(row, parent_shots_column)
            if item and item.text():
                parent_shot = item.text()
                if parent_shot not in parent_shot_list:
                    parent_shot_list.append(parent_shot)

        # TODO: REMOVE THIS
        logger.info('Checking shotgun for existing parent shots')

        # check shotgun for parent shots that may have been previously created
        # make a list of any shots that need to be created
        create_new_shot_list = list()
        for parent_shot in parent_shot_list:
            # find in shotgun
            shot = self.sg.find_one('Shot', [['project', 'is', self.project], ['code', 'is', parent_shot]])
            if not shot and parent_shot not in create_new_shot_list:
                create_new_shot_list.append(parent_shot)

        logger.info('Adding parent shots to table')

        # add new shots to table
        for shot_name in create_new_shot_list:
            row = 0
            col = self.header_list.index('Parent Shots')
            # find the shot in the table, we need the row number
            for row in range(1, self.ui.table.rowCount() + 1):
                item = self.ui.table.item(row, col)
                if item.text() == shot_name:
                    break
            if row == 0:
                continue
            episode_item = self.ui.table.item(row, self.header_list.index('Episode'))
            sequence_item = self.ui.table.item(row, self.header_list.index('Sequence'))
            episode = ''
            sequence = ''
            if episode_item:
                episode = episode_item.text()
            if sequence_item:
                sequence = sequence_item.text()
            # find where to insert new shot row
            # loop through rows until an "Element" is found
            # we will insert new shot row before the element row
            new_row_number = 1
            for new_row_number in range(1, self.ui.table.rowCount()):
                item = self.ui.table.item(new_row_number, self.header_list.index('Entity Type'))
                if item.text() == 'Element':
                    break
            self.ui.table.insertRow(new_row_number)
            # add new shot to table
            for header_name in self.header_list:
                item = QtGui.QTableWidgetItem()
                if header_name == 'Episode':
                    item.setText(episode)
                elif header_name == 'Sequence':
                    item.setText(sequence)
                elif header_name == 'Shot Code':
                    item.setText(shot_name)
                elif header_name == 'Entity Type':
                    item.setText('Shot')
                self.ui.table.setItem(new_row_number, self.header_list.index(header_name), item)
            checkbox = QtGui.QCheckBox()
            checkbox.setCheckState(QtCore.Qt.Checked)
            self.ui.table.setCellWidget(new_row_number, self.ui.table.columnCount()-1, checkbox)

        # remove all flags to disable item
        # item.setFlags(QtCore.Qt.NoItemFlags)
        # set a flag
        # note this will wipe out other flag values
        # item.setFlags(QtCore.Qt.ItemIsSelectable)
        # set several flags
        # item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEditable)
        self.setMinimumSize(1500, 540)
        self.resize(1500, 540)
        self.ui.button_file_open.hide()
        self.ui.button_shotgun_import.show()
        self.update()

    def _delete_table_rows(self):
        """Recursively delete table rows"""
        for row in range(self.ui.table.rowCount()):
            self.ui.table.removeRow(row)
        if self.ui.table.rowCount():
            self._delete_table_rows()

    def _fix_line_terminators(self, edl_file_path):
        """Fix line terminators to work on all platforms.
        :param edl_file_path: str
        :return: str
        """
        logger.info('Fixing line terminators for edl file')
        dir_path, base_name = os.path.split(edl_file_path)
        base_name = base_name.strip().replace(' ', '_')
        edl_process_path = os.path.join(dir_path, 'edl_process')
        edl_file_copy = os.path.join(edl_process_path, base_name)

        # make edl process directory
        if os.path.isdir(dir_path):
            if not os.path.exists(edl_process_path):
                try:
                    os.mkdir(edl_process_path)
                except WindowsError:
                    msg = 'ERROR: cannot create edl_process directory'
                    logger.info(msg)
                    self.ui.label_status.setText(msg)
                    return

        # read existing file
        with open(edl_file_path, 'r') as f:
            lines = f.read()

        # convert from old macintosh to all platforms
        if '\n' not in lines and '\r' in lines:
            new_lines = re.sub('\r', '\n', lines)
            with open(edl_file_copy, 'w') as f:
                f.write(new_lines)
                return edl_file_copy

        # if line terminators are okay for windows
        elif '\r\n' in lines and sys.platform == 'win32':
            os.system('copy {} {}'.format(edl_file_path, edl_file_copy))
            return edl_file_copy

        # convert from linux to windows
        elif '\r' not in lines and '\n' in lines and sys.platform == 'win32':
            with open(edl_file_copy, 'w') as f:
                f.write(lines)
                return edl_file_copy

        # if line terminators are okay for linux
        elif '\n' in lines and sys.platform.startswith('linux'):
            os.system('cp {} {}'.format(edl_file_path, edl_file_copy))
            return edl_file_copy

        # otherwise something went wrong
        else:
            msg = 'ERROR: cannot fix line terminators'
            self.ui.label_status.setText(msg)
            logger.info(msg)
            return None

    @staticmethod
    def get_headers():
        return ['Episode',
                'Sequence',
                'Shot Code',
                'Cut Duration',
                'EDL Clip Name',
                'EDL Timecode Start',
                'EDL Timecode End',
                'EDL REC Timecode Start',
                'EDL REC Timecode End',
                'Nuke CC',
                'Parent Shots',
                'Entity Type',
                'Import']

    def _parse_edl(self, edl_file_path):
        """
        :param edl_file_path: str
        :return:
        """
        if not self.first_time:
            self._delete_table_rows()
            self.edl_data = None
            self.element_list = list()
            self.output_file_name = None

        self.output_file_name = os.path.basename(edl_file_path)[:-4].replace(' ', '_')

        master_list = list()
        element_list = list()

        parser = Parser(self.fps)

        with open(edl_file_path) as f:
            edl = parser.parse(f)
            for event in edl.events:
                event_dict = dict()
                event_dict['EDL Clip Name'] = str(event.reel)
                event_dict['Cut Duration'] = str(event.rec_length() + 1)
                event_dict['EDL Event Number'] = str(event.num)  # unused
                event_dict['EDL Timecode Start'] = str(event.src_start_tc)
                event_dict['EDL Timecode End'] = str(event.src_end_tc)
                event_dict['EDL REC Timecode Start'] = str(event.rec_start_tc)
                event_dict['EDL REC Timecode End'] = str(event.rec_end_tc)
                # parse nuke cc data
                nuke_cc = str()
                for comment in event.comments:
                    if comment.startswith('* ASC_SOP'):
                        nuke_cc += str(comment)
                    elif comment.startswith('* ASC_SAT'):
                        nuke_cc += ' ' + str(comment)
                if not nuke_cc:
                    nuke_cc = 'unavailable'
                event_dict['Nuke CC'] = nuke_cc
                event_dict['Entity Type'] = 'Element'  # default entity type
                event_dict['Parent Shots'] = ''
                # parse shot name
                m = self.rgx_clip_name.match(str(event.reel).strip())
                if m:
                    shot, element, extra = m.groups()
                    shot = str(shot.strip())
                    element = str(element.strip())
                    extra = str(extra.strip())
                    if element:
                        shot_code = shot + element + extra
                        event_dict['Parent Shots'] = shot
                    else:
                        shot_code = shot
                        event_dict['Entity Type'] = 'Shot'
                    event_dict['Shot Code'] = shot_code
                    event_dict['Episode'] = shot_code[0:1]
                    event_dict['Sequence'] = shot_code[0:3]
                # if the shot does not have a parent, it is a master plate
                if not event_dict['Parent Shots']:
                    master_list.append(event_dict)
                # otherwise, this is a shot element
                else:
                    element_list.append(event_dict)

        event_list = master_list + element_list

        if not event_list:
            msg = 'ERROR: no edl data'
            logger.info(msg)
            self.ui.label_status.setText(msg)
            return

        # create qt table
        self.edl_data = event_list
        self._create_table()

        msg = 'Hey {}, good work!'.format(self.user_first_name)
        self.ui.label_status.setText(msg)

    def _select_edl_file(self):
        """Select edl file and parse it via self._edl_parse
        :return: None
        """
        logger.info('Starting EDL file selection')
        self.ui.label_status.setText('Selecting EDL file')

        start_path = '~'
        if self.last_edl_file_path:
            start_path = self.last_edl_file_path
        else:
            # 'darwin'  'linux'  'win32'
            if sys.platform == 'win32':
                start_path = 'C:\\'
        try:
            dial = QtGui.QFileDialog().getOpenFileName(self, u"Choose file", start_path, "*.edl")
        except IOError:
            msg = 'ERROR: failed to get path from file dialog.'
            logger.info(msg)
            self.ui.label_status.setText(msg)
            return

        if dial and dial[0]:
            self.last_edl_file_path = os.path.dirname(dial[0])
            edl_file_path = self._fix_line_terminators(dial[0])
            if edl_file_path:
                if not os.path.exists(edl_file_path):
                    msg = 'ERROR: eld file path does not exist.'
                    logger.info(msg)
                    self.ui.label_status.setText(msg)
                    return
                # success, parse edl file
                self._parse_edl(edl_file_path)
                return
            else:
                msg = 'ERROR: failed to get eld file path.'
                logger.info(msg)
                self.ui.label_status.setText(msg)
                return
        else:
            msg = 'WARNING: no path from file dialog. User may have canceled.'
            logger.info(msg)
            self.ui.label_status.setText('')
            return

    def _set_row_color(self, row, color_name):
        """Set row color based on name
        :param row: int
        :param color_name: str
        :return: None
        """
        if color_name == 'bright green':
            row_color = QtGui.QColor(240, 255, 220)
        elif color_name == 'green':
            row_color = QtGui.QColor(200, 255, 200)
        elif color_name == 'blue':
            row_color = QtGui.QColor(140, 150, 220)
        elif color_name == 'light blue':
            row_color = QtGui.QColor(240, 240, 255)
        elif color_name == 'red':
            row_color = QtGui.QColor(200, 0, 0)
        elif color_name == 'dark red':
            row_color = QtGui.QColor(128, 0, 0)
        elif color_name == 'violet':
            row_color = QtGui.QColor(240, 230, 255)
        elif color_name == 'ultra_violet':
            row_color = QtGui.QColor(230, 210, 255)
        elif color_name == 'gray':
            row_color = QtGui.QColor(200, 200, 200)
        elif color_name == 'dark gray':
            row_color = QtGui.QColor(48, 48, 48)
        elif color_name == 'yellow':
            row_color = QtGui.QColor(112, 112, 0)
        else:
            # default
            row_color = QtGui.QColor(128, 128, 128)

        for col in range(self.ui.table.columnCount()):
            item = self.ui.table.item(row, col)
            if item:
                item.setBackground(row_color)

    def _shotgun_import(self):
        """
        :return:
        """
        logger.info('Starting shotgun import process')

        all_shot_data = list()
        table_headers = list()

        # get table headers
        for col in range(self.ui.table.columnCount()):
            item = self.ui.table.item(0, col)
            table_headers.append(item.text())

        import_column = table_headers.index('Import')

        # loop through table and collect all shot data
        for row in range(1, self.ui.table.rowCount()):
            # determine if user wants to import shot
            checkbox = self.ui.table.cellWidget(row, import_column)
            import_this_row = True
            if not checkbox.isChecked():
                import_this_row = False
            # add data to dictionary
            data_dict = dict()
            for col in range(self.ui.table.columnCount()):
                item = self.ui.table.item(row, col)
                if item:
                    val = item.text()
                else:
                    val = ''
                if col == import_column:
                    if import_this_row:
                        val = 'YES'
                    else:
                        val = 'NO'
                # use header name for dict key
                k = table_headers[col]
                data_dict[k] = val
            data_dict['row_number'] = row
            all_shot_data.append(data_dict)

        # close gui connection to shotgun, we'll use thread connection
        self.sg.close()
        self.sg = None

        self.ui.progress_bar.setMaximum(len(all_shot_data))
        self.ui.progress_bar.update()
        self.ui.progress_bar.show()
        self.ui.button_shotgun_import.hide()

        msg = 'Starting thread'
        logger.info(msg)

        # thread process data for shotgun
        self._thread = SGProcessThread(shot_data_list=all_shot_data)
        self._thread.finished.connect(self._thread_notify_finish)
        self._thread.signal_from_thread.connect(self._thread_receive)
        self._thread.start()

    def _start_over(self):
        msg = 'EDL import complete'
        self.ui.label_status.setText(msg)
        self.ui.progress_bar.hide()
        self.ui.button_file_open.show()
        self.sg = self._app.shotgun
        self.first_time = False
        self.update()

    def _thread_receive(self, shot_code, msg, count):
        # receive message from thread
        self.ui.progress_bar.setValue(count)
        self.ui.progress_bar.update()
        shot_name_column = self.get_headers().index('Shot Code')
        item = self.ui.table.item(count, shot_name_column)
        if item:
            if item.text() == shot_code and msg == 'imported':
                self._set_row_color(count, 'green')
            elif item.text() == shot_code and msg == 'exists':
                self._set_row_color(count, 'blue')
            elif item.text() == shot_code and msg == 'error':
                self._set_row_color(count, 'red')
            elif item.text() == shot_code and msg == 'test':
                self._set_row_color(count, 'yellow')
            elif item.text() == shot_code and msg == 'skip':
                self._set_row_color(count, 'dark gray')
        # set next row to bright green
        if count < self.ui.table.rowCount():
            self._set_row_color(count + 1, 'bright green')
        message = 'thread processed row {}:  {}  {}'.format(count, shot_code, msg)
        logger.info(message)
        self.ui.label_status.setText(message)
        self.update()

    def _thread_notify_finish(self):
        self._thread = None
        logger.info('Thread finished')
        self._start_over()

    def _thread_send(self, shot_data_list):
        # send shot list to thread
        self.app_signals.from_gui.emit(shot_data_list)


class SGProcessThread(QtCore.QThread):
    """Thread to create/import elements and shots in Shotgun."""

    # note signal must be created before thread initialization
    signal_from_thread = QtCore.Signal(str, str, int)

    def __init__(self, shot_data_list):
        """Initialize thread.
        :param shot_data_list: list of dictionaries
        """
        QtCore.QThread.__init__(self)
        self.shot_data_list = shot_data_list
        self._app = sgtk.platform.current_bundle()
        self.project = self._app.context.project
        self.user = self._app.context.user

        # sg connection
        self.sg = self._app.shotgun

        # TODO: TURN OFF TEST MODE
        self.test = True
        if self.project['id'] == 243:  # TEST_DEV_01
            self.test = False

    def __del__(self):
        self.wait()

    def run(self):
        """Process shot data, create new elements / shots in Shotgun.
        :return: None
        """
        for shot_data in self.shot_data_list:
            # process depending on entity type
            if shot_data['Entity Type'] == 'Element':
                self.process_element(shot_data)
            elif shot_data['Entity Type'] == 'Shot':
                self.process_shot(shot_data)

        self.sg.close()
        self.sg = None

    def process_element(self, element_data):
        """
        :param element_data: dict
        :return: nothing
        """
        element_code = element_data['Shot Code']
        row_number = element_data['row_number']

        if self.test:
            status = 'test'
            self.signal_from_thread.emit(element_code, status, row_number)
            return

        if element_data['Import'] == 'NO':
            status = 'skip'
            self.signal_from_thread.emit(element_code, status, row_number)
            return

        # check if element already exists in shotgun
        filters = [['code', 'is', element_code], ['project', 'is', self.project]]
        result = self.sg.find_one('Element', filters, ['code'])
        # if the element already exists don't process it
        if result:
            self.signal_from_thread.emit(element_code, 'exists', row_number)
            return

        # find parent shot
        parent_shot = None

        if element_data['Parent Shots']:
            parent_shot_name = element_data['Parent Shots']
            parent_filters = [['code', 'is', parent_shot_name], ['project', 'is', self.project]]
            parent_shot = self.sg.find_one('Shot', parent_filters, ['code'])

        # element creation data
        element_create_data = {'code': element_code, 'project': self.project}
        if element_data['Cut Duration']:
            element_create_data['sg_cut_duration'] = int(element_data['Cut Duration'])
        if element_data['EDL Clip Name']:
            element_create_data['sg_edl_clip_name'] = element_data['EDL Clip Name']
        if element_data['EDL Timecode Start']:
            element_create_data['sg_edl_timecode_start'] = element_data['EDL Timecode Start']
        if element_data['EDL Timecode End']:
            element_create_data['sg_edl_timecode_end'] = element_data['EDL Timecode End']
        if element_data['EDL REC Timecode Start']:
            element_create_data['sg_edl_rec_timecode_start'] = element_data['EDL REC Timecode Start']
        if element_data['EDL REC Timecode End']:
            element_create_data['sg_edl_rec_timecode_end'] = element_data['EDL REC Timecode End']
        if element_data['Nuke CC']:
            element_create_data['sg_nuke_cc'] = element_data['Nuke CC']
        if parent_shot:
            element_create_data['shots'] = [parent_shot]

        # create element
        new_element = self.sg.create('Element', element_create_data)

        status = 'imported'
        if not new_element:
            status = 'error'

        self.signal_from_thread.emit(element_code, status, row_number)

    def process_shot(self, shot_data):
        """
        :param shot_data:
        :return: nothing
        """
        shot_code = shot_data['Shot Code']
        row_number = shot_data['row_number']

        if self.test:
            status = 'test'
            self.signal_from_thread.emit(shot_code, status, row_number)
            return

        if shot_data['Import'] == 'NO':
            status = 'skip'
            self.signal_from_thread.emit(shot_code, status, row_number)
            return

        # check if shot already exists in shotgun
        filters = [['code', 'is', shot_code], ['project', 'is', self.project]]
        result = self.sg.find_one('Shot', filters, ['code'])

        # if the shot already exists we don't need to process it
        if result:
            self.signal_from_thread.emit(shot_code, 'exists', row_number)
            return

        sequence_name = shot_data['Sequence']

        # get the sequence
        filters = [['code', 'is', sequence_name], ['project', 'is', self.project]]
        sequence = self.sg.find_one('Sequence', filters, ['code', 'episode'])

        # create new sequence if necessary
        if not sequence:
            seq_create_data = {'code': sequence_name, 'project': self.project}
            sequence = self.sg.create('Sequence', seq_create_data, ['code', 'episode'])

        if not sequence:
            status = 'error'
            self.signal_from_thread.emit(shot_code, status, row_number)
            return

        # shot creation data
        shot_create_data = {'code': shot_code, 'project': self.project, 'sg_sequence': sequence}
        if shot_data['Cut Duration']:
            shot_create_data['sg_cut_duration'] = int(shot_data['Cut Duration'])
        if shot_data['EDL Clip Name']:
            shot_create_data['sg_edl_clip_name'] = shot_data['EDL Clip Name']
        if shot_data['EDL Timecode Start']:
            shot_create_data['sg_edl_timecode_start'] = shot_data['EDL Timecode Start']
        if shot_data['EDL Timecode End']:
            shot_create_data['sg_edl_timecode_end'] = shot_data['EDL Timecode End']
        if shot_data['EDL REC Timecode Start']:
            shot_create_data['sg_edl_rec_timecode_start'] = shot_data['EDL REC Timecode Start']
        if shot_data['EDL REC Timecode End']:
            shot_create_data['sg_edl_rec_timecode_end'] = shot_data['EDL REC Timecode End']
        if shot_data['Nuke CC']:
            shot_create_data['sg_nuke_cc'] = shot_data['Nuke CC']

        # create shot
        try:
            new_shot = self.sg.create('Shot', shot_create_data)
            status = 'imported'
            if not new_shot:
                status = 'error'
            self.signal_from_thread.emit(shot_code, status, row_number)
        except sgtk.TankError:
            self.signal_from_thread.emit(shot_code, 'error', row_number)
