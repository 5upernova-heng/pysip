import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QProgressBar, QMessageBox, QGroupBox, QComboBox)
from PyQt6.QtCore import pyqtSignal, QObject, QTimer, Qt
import pjsua2 as pj

# ==========================================
# 1. 定义信号类 (跨线程通信)
# ==========================================
class WorkerSignals(QObject):
    call_state_changed = pyqtSignal(str, bool) # 状态文本, 是否已连接
    call_media_active = pyqtSignal(bool)       # 媒体是否激活
    incoming_call = pyqtSignal(str)            # 新来电信号 (参数: 对方URI)

# ==========================================
# 2. PJSIP 类定义 (Backend)
# ==========================================

class MyCall(pj.Call):
    def __init__(self, acc, call_id=pj.PJSUA_INVALID_ID, signals=None):
        pj.Call.__init__(self, acc, call_id)
        self.signals = signals
        self.audio_media = None 

    def onCallState(self, prm):
        try:
            ci = self.getInfo()
            state_text = ci.stateText
            # PJSIP_INV_STATE_CONFIRMED = 5
            is_connected = (ci.state == pj.PJSIP_INV_STATE_CONFIRMED)
            
            if self.signals:
                self.signals.call_state_changed.emit(state_text, is_connected)
        except Exception as e:
            print(f"Error in onCallState: {e}")

    def onCallMediaState(self, prm):
        try:
            ci = self.getInfo()
            for mi in ci.media:
                if mi.type == pj.PJMEDIA_TYPE_AUDIO and \
                   (mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE or \
                    mi.status == pj.PJSUA_CALL_MEDIA_REMOTE_HOLD):
                    
                    med = self.getAudioMedia(mi.index)
                    self.audio_media = med
                    
                    ep = pj.Endpoint.instance()
                    adm = ep.audDevManager()
                    
                    # 音频路由：连接麦克风和扬声器
                    med.startTransmit(adm.getPlaybackDevMedia())
                    adm.getCaptureDevMedia().startTransmit(med)
                    
                    if self.signals:
                        self.signals.call_media_active.emit(True)
                    return
        except Exception as e:
            print(f"Error in onCallMediaState: {e}")

class MyAccount(pj.Account):
    def __init__(self, signals):
        pj.Account.__init__(self)
        self.signals = signals
        self.current_call = None # 追踪当前通话

    def onIncomingCall(self, prm):
        # 如果当前已经有通话，直接拒接 (Busy Here)
        if self.current_call and self.current_call.isActive():
            tmp_call = MyCall(self, prm.callId)
            op = pj.CallOpParam()
            op.statusCode = 486 # Busy Here
            tmp_call.hangup(op)
            return

        # 初始化来电对象 (但不接听)
        self.current_call = MyCall(self, prm.callId, self.signals)
        
        # 获取对方信息
        try:
            ci = self.current_call.getInfo()
            remote_uri = ci.remoteUri
        except:
            remote_uri = "Unknown"

        # 通知 UI 线程有人来电
        self.signals.incoming_call.emit(remote_uri)

# ==========================================
# 3. PyQt6 主窗口 (Frontend)
# ==========================================
class SipPhoneApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 PJSIP Softphone")
        self.resize(450, 550)
        
        # 信号连接
        self.signals = WorkerSignals()
        self.signals.call_state_changed.connect(self.update_status)
        self.signals.call_media_active.connect(self.enable_audio_metering)
        self.signals.incoming_call.connect(self.handle_incoming_call) # 连接来电信号

        self.init_pjsip()
        self.init_ui()
        self.populate_audio_devices()

        self.meter_timer = QTimer()
        self.meter_timer.interval = 100 
        self.meter_timer.timeout.connect(self.update_audio_levels)

    def init_pjsip(self):
        try:
            self.ep = pj.Endpoint()
            self.ep.libCreate()

            ep_cfg = pj.EpConfig()
            ep_cfg.logConfig.level = 4
            ep_cfg.logConfig.consoleLevel = 4
            self.ep.libInit(ep_cfg)

            t_cfg = pj.TransportConfig()
            t_cfg.port = 5060
            self.ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, t_cfg)

            self.ep.libStart()

            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = "sip:0.0.0.0" # 监听所有接口
            self.acc = MyAccount(self.signals)
            self.acc.create(acc_cfg)
            
        except pj.Error as e:
            QMessageBox.critical(self, "Init Error", f"Error: {e.info()}")
            sys.exit(1)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # --- 设备设置 ---
        dev_group = QGroupBox("音频设备")
        dev_layout = QVBoxLayout()
        hbox_in = QHBoxLayout()
        hbox_in.addWidget(QLabel("麦克风:"))
        self.combo_mic = QComboBox()
        self.combo_mic.currentIndexChanged.connect(self.on_mic_changed)
        hbox_in.addWidget(self.combo_mic)
        dev_layout.addLayout(hbox_in)

        hbox_out = QHBoxLayout()
        hbox_out.addWidget(QLabel("扬声器:"))
        self.combo_spk = QComboBox()
        self.combo_spk.currentIndexChanged.connect(self.on_spk_changed)
        hbox_out.addWidget(self.combo_spk)
        dev_layout.addLayout(hbox_out)

        btn_refresh = QPushButton("刷新设备列表")
        btn_refresh.clicked.connect(self.populate_audio_devices)
        dev_layout.addWidget(btn_refresh)
        dev_group.setLayout(dev_layout)
        layout.addWidget(dev_group)

        # --- 通话控制 ---
        dial_group = QGroupBox("通话控制")
        dial_layout = QVBoxLayout()
        
        # IP 输入行
        hbox_ip = QHBoxLayout()
        self.lbl_ip = QLabel("目标 IP:")
        hbox_ip.addWidget(self.lbl_ip)
        self.txt_ip = QLineEdit()
        self.txt_ip.setPlaceholderText("例如 192.168.1.5")
        hbox_ip.addWidget(self.txt_ip)
        dial_layout.addLayout(hbox_ip)

        # 按钮行
        btn_layout = QHBoxLayout()
        
        self.btn_call = QPushButton("拨打")
        self.btn_call.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        self.btn_call.clicked.connect(self.do_call)
        btn_layout.addWidget(self.btn_call)

        self.btn_answer = QPushButton("接听")
        self.btn_answer.setStyleSheet("background-color: #2196F3; color: white; padding: 10px;")
        self.btn_answer.clicked.connect(self.do_answer)
        self.btn_answer.hide() # 默认隐藏
        btn_layout.addWidget(self.btn_answer)

        self.btn_hangup = QPushButton("挂断")
        self.btn_hangup.setStyleSheet("background-color: #F44336; color: white; padding: 10px;")
        self.btn_hangup.clicked.connect(self.do_hangup)
        self.btn_hangup.setEnabled(False)
        btn_layout.addWidget(self.btn_hangup)

        dial_layout.addLayout(btn_layout)
        dial_group.setLayout(dial_layout)
        layout.addWidget(dial_group)

        # --- 状态显示 ---
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("padding: 15px; font-size: 16px; background: #eee; border-radius: 5px;")
        layout.addWidget(self.lbl_status)

        # --- 音量条 ---
        audio_group = QGroupBox("通话状态")
        audio_layout = QVBoxLayout()
        audio_layout.addWidget(QLabel("麦克风 (Tx):"))
        self.bar_mic = QProgressBar()
        self.bar_mic.setRange(0, 100)
        self.bar_mic.setValue(0)
        audio_layout.addWidget(self.bar_mic)
        
        audio_layout.addWidget(QLabel("对方声音 (Rx):"))
        self.bar_speaker = QProgressBar()
        self.bar_speaker.setRange(0, 100)
        self.bar_speaker.setValue(0)
        audio_layout.addWidget(self.bar_speaker)
        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)

        layout.addStretch()

    # ================= 逻辑方法 =================

    def populate_audio_devices(self):
        self.combo_mic.blockSignals(True)
        self.combo_spk.blockSignals(True)
        self.combo_mic.clear()
        self.combo_spk.clear()
        try:
            adm = self.ep.audDevManager()
            adm.refreshDevs()
            devs = adm.enumDev() 
            current_cap = adm.getCaptureDev()
            current_play = adm.getPlaybackDev()

            for i, dev in enumerate(devs):
                dev_name = dev.name
                # 中文乱码修复
                try:
                    raw_bytes = dev_name.encode('utf-8', 'surrogateescape')
                    dev_name = raw_bytes.decode('gbk')
                except: pass

                display = f"[{i}] {dev_name}"
                if dev.inputCount > 0:
                    self.combo_mic.addItem(display, i)
                    if i == current_cap: self.combo_mic.setCurrentIndex(self.combo_mic.count()-1)
                if dev.outputCount > 0:
                    self.combo_spk.addItem(display, i)
                    if i == current_play: self.combo_spk.setCurrentIndex(self.combo_spk.count()-1)
        except: pass
        self.combo_mic.blockSignals(False)
        self.combo_spk.blockSignals(False)

    def on_mic_changed(self, index):
        if index < 0: return
        try: self.ep.audDevManager().setCaptureDev(self.combo_mic.itemData(index))
        except: pass

    def on_spk_changed(self, index):
        if index < 0: return
        try: self.ep.audDevManager().setPlaybackDev(self.combo_spk.itemData(index))
        except: pass

    # --- 通话核心逻辑 ---

    def do_call(self):
        target = self.txt_ip.text().strip()
        if not target: return
        uri = f"sip:{target}" if "@" in target else f"sip:{target}"
        
        try:
            self.acc.current_call = MyCall(self.acc, signals=self.signals)
            prm = pj.CallOpParam(True)
            self.acc.current_call.makeCall(uri, prm)
            self.set_ui_state_dialing()
        except pj.Error as e:
            self.lbl_status.setText(f"错误: {e.info()}")

    def handle_incoming_call(self, remote_uri):
        """ 处理来电信号 (Running on Main Thread) """
        # 弹窗提示或更改界面
        self.lbl_status.setText(f"☎️ 来电中: {remote_uri}")
        self.lbl_status.setStyleSheet("background: #FFF3CD; color: #856404; padding: 15px; font-size: 16px; font-weight: bold;")
        
        # 切换按钮显示
        self.btn_call.hide()
        self.txt_ip.setEnabled(False)
        self.btn_answer.show() # 显示接听
        self.btn_hangup.setEnabled(True) # 挂断按钮此时充当“拒接”

        # 可以在这里加个播放铃声的逻辑
        QApplication.alert(self) # 让任务栏图标闪烁

    def do_answer(self):
        """ 点击接听按钮 """
        if self.acc.current_call:
            prm = pj.CallOpParam()
            prm.statusCode = 200 # OK
            try:
                self.acc.current_call.answer(prm)
            except pj.Error as e:
                print(f"Answer error: {e.info()}")
        
        # UI 切换为通话中
        self.btn_answer.hide()
        self.btn_hangup.setEnabled(True)

    def do_hangup(self):
        """ 点击挂断（或拒接） """
        if self.acc.current_call:
            prm = pj.CallOpParam(True)
            try:
                self.acc.current_call.hangup(prm)
            except: pass
        self.reset_ui()

    # --- 辅助方法 ---

    def update_status(self, text, is_connected):
        self.lbl_status.setText(text)
        if "DISCONN" in text or "Terminated" in text:
            self.reset_ui()
        elif is_connected:
            self.lbl_status.setStyleSheet("background: #D4EDDA; color: #155724; padding: 15px; font-size: 16px; border: 1px solid green;")
            self.btn_answer.hide()
            self.btn_call.hide()
            self.btn_hangup.setEnabled(True)

    def reset_ui(self):
        """ 恢复到空闲状态 """
        self.meter_timer.stop()
        self.bar_mic.setValue(0)
        self.bar_speaker.setValue(0)
        
        self.btn_call.show()
        self.btn_call.setEnabled(True)
        self.btn_answer.hide()
        self.btn_hangup.setEnabled(False)
        self.txt_ip.setEnabled(True)
        
        self.lbl_status.setText("就绪")
        self.lbl_status.setStyleSheet("padding: 15px; font-size: 16px; background: #eee;")

    def set_ui_state_dialing(self):
        self.btn_call.setEnabled(False)
        self.btn_hangup.setEnabled(True)
        self.txt_ip.setEnabled(False)

    def enable_audio_metering(self, active):
        if active: self.meter_timer.start()
        else: self.meter_timer.stop()

    def update_audio_levels(self):
        if self.acc.current_call and self.acc.current_call.isActive() and self.acc.current_call.audio_media:
            try:
                tx = self.acc.current_call.audio_media.getTxLevel()
                rx = self.acc.current_call.audio_media.getRxLevel()
                self.bar_mic.setValue(int(tx / 2.55))
                self.bar_speaker.setValue(int(rx / 2.55))
            except: pass

    def closeEvent(self, event):
        try: self.ep.libDestroy()
        except: pass
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = SipPhoneApp()
    win.show()
    sys.exit(app.exec())