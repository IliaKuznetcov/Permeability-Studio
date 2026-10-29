"""Selected-pulse analysis with explicit inputs, tail selection and diagnostic plots."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from threading import Event
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter, QVBoxLayout,
    QWidget, QTextBrowser,
)
from .calibration_view import number
from .models import PRESSURE_UNITS
from .pressure_widgets import PressureSpinBox
from .pressure_display import pressure_text
from .permeability import (
    AnalysisSettings, MICRODARCY_M2, extract_trace, late_time, history_match,
    analysis_fingerprint, _finite_list,
)


class AnalysisWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, trace, settings, method, parent=None):
        super().__init__(parent)
        self.trace, self.settings, self.method = trace, settings, method
        self.cancel = Event()

    def run(self):
        try:
            if self.method == 'late':
                result = late_time(self.trace, self.settings)
            else:
                result = history_match(self.trace, self.settings, cancel=self.cancel,
                                       progress=self.progress.emit)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class PermeabilityDialog(QDialog):
    """One captured record at a time; computed results are stored in its project."""

    def __init__(self, controller, pulse):
        super().__init__(controller.view)
        self.controller, self.pulse = controller, pulse
        self.project = controller.project
        controller.restore_project_settings()
        self.worker = None
        self.pending_result = None
        self.result = None
        self.syncing = False
        self.pressure_unit='kPa'
        self.setWindowTitle(f'Permeability analysis — {pulse.label}')
        available = self.screen().availableGeometry()
        self.resize(min(1450, int(available.width()*.94)),
                    min(960, int(available.height()*.90)))
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        root = QVBoxLayout(self)
        note = QLabel('Analyze the captured record, including its own recording snapshot. '
                      'Check the pressure channels and enter measured geometry, porosity and connected volumes. '
                      'Negative gauge readings under vacuum are valid; model plots and calculations use absolute pressure. '
                      'FDM assumes an initially downstream-equilibrated core and constant-temperature ideal gas.')
        note.setWordWrap(True)
        root.addWidget(note)
        setup=QHBoxLayout()
        self.display_units=QComboBox();self.display_units.addItems(['kPa','psi'])
        self.display_units.setCurrentText(controller.main.model.pressure_display_unit)
        self.recording_basis=QComboBox();self.recording_basis.addItems(['Gauge','Absolute'])
        self.recording_basis.setCurrentText(self.project.pressure_basis)
        self.barometric=PressureSpinBox(0,1e5,self.project.barometric_kpa or 0,6,'Not entered')
        for label,widget in [('Pressure display',self.display_units),('Recording basis (session)',self.recording_basis),('Measured atmospheric P',self.barometric)]:
            setup.addWidget(QLabel(label));setup.addWidget(widget)
        root.addLayout(setup)
        self.atmospheric_readout=QLabel()
        self.atmospheric_readout.setWordWrap(True)
        root.addWidget(self.atmospheric_readout)
        self.update_atmospheric_readout()
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(320)
        panel = QWidget()
        scroll.setWidget(panel)
        left = QVBoxLayout(panel)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.controls = {}
        snapshot = self.project.snapshots[pulse.dataset_id]
        pressure_channels = [c for c in snapshot.channels if c.quantity == 'Pressure']
        for name, label, location in [('upstream_key','Upstream pressure','upstream'),
                                      ('downstream_key','Downstream pressure','downstream')]:
            combo = QComboBox()
            combo.addItem('Choose channel…', '')
            for c in pressure_channels:
                combo.addItem(f'{c.source_column} [{c.unit}] — {c.location}', c.key)
            # Only suggest a channel when its assigned location is explicit.
            candidates = [c.key for c in pressure_channels if c.location.lower() == location]
            if len(candidates) == 1:
                combo.setCurrentIndex(combo.findData(candidates[0]))
            self.controls[name] = combo
            form.addRow(label, combo)
        defaults = {
            'length_m': (50.0, 'Core length (mm)', .001, 10000, 4),
            'diameter_m': (25.4, 'Core diameter (mm)', .001, 10000, 4),
            'porosity': (.1, 'Porosity (fraction)', .00001, .99999, 5),
            'upstream_volume_m3': (5, 'Connected upstream volume (cm³)', .00001, 1e7, 5),
            'downstream_volume_m3': (5, 'Connected downstream volume (cm³)', .00001, 1e7, 5),
            'viscosity_pa_s': (18, 'Gas viscosity (µPa·s)', .001, 10000, 5),
            'slip_pa': (0, 'Fixed slip factor b (kPa)', 0, 1e5, 5),
            'initial_k_m2': (10, 'Initial k (µD)', 1e-7, 1e8, 7),
            'min_k_m2': (.001, 'Minimum k (µD)', 1e-8, 1e8, 8),
            'max_k_m2': (10000, 'Maximum k (µD)', 1e-7, 1e10, 5),
            'cells': (40, 'FDM spatial intervals', 4, 400, 0),
            'max_steps': (250000, 'Maximum FDM steps per solve', 100, 10000000, 0),
            'safety_factor': (.8, 'Timestep safety factor', .01, .8, 3),
            'sigma_up_pa': (.1, 'Upstream noise σ (kPa)', .000001, 1e4, 6),
            'sigma_down_pa': (.1, 'Downstream noise σ (kPa)', .000001, 1e4, 6),
            'delta_floor_pa': (.1, 'Late-time ΔP floor (kPa)', .000001, 1e4, 6),
            'upstream_pressure_correction_pa': (0,'Upstream sensor correction (+kPa)',-1e4,1e4,6),
            'downstream_pressure_correction_pa': (0,'Downstream sensor correction (+kPa)',-1e4,1e4,6),
        }
        for name, (value, label, low, high, decimals) in defaults.items():
            spin = number(low, high, value, decimals)
            self.controls[name] = spin
            if name=='upstream_pressure_correction_pa':form.insertRow(2,label,spin)
            elif name=='downstream_pressure_correction_pa':form.insertRow(3,label,spin)
            else:form.addRow(label, spin)
        for name,label,choices in [
            ('initial_upstream_mode','Before valve opening',('Isolated tank','Combined storage')),
            ('dead_pressure_mode','Initial DU pressure source',('Downstream initial','Entered absolute pressure')),
        ]:
            combo=QComboBox()
            for choice in choices:
                combo.addItem(choice,choice)
            self.controls[name]=combo
        self.controls['upstream_dead_volume_m3']=number(0,1e7,0,5)
        self.controls['initial_dead_pressure_pa']=number(.000001,1e6,101.325,6)
        for row,(name,label) in enumerate([
            ('initial_upstream_mode','Before valve opening'),
            ('upstream_dead_volume_m3','Upstream dead volume DU (cm³)'),
            ('dead_pressure_mode','Initial DU pressure source'),
            ('initial_dead_pressure_pa','Entered initial DU pressure (kPa abs)'),
        ],start=9):
            form.insertRow(row,label,self.controls[name])
        self.controls['fdm_backend']=QComboBox()
        for choice in ('Adaptive BDF','Reference explicit'):
            self.controls['fdm_backend'].addItem(choice,choice)
        form.insertRow(form.getWidgetPosition(self.controls['cells'])[0],
                       'FDM time integrator',self.controls['fdm_backend'])
        self.controls['fdm_backend'].setToolTip('Adaptive BDF uses implicit adaptive timesteps for the same spatial FDM equations. Reference explicit retains the original verified Euler solver. Compare time/grid convergence before reporting results.')
        self.budget_label=form.labelForField(self.controls['max_steps'])
        left.addLayout(form)
        self.pressure_labels={name:form.labelForField(self.controls[name]) for name in
            ('slip_pa','sigma_up_pa','sigma_down_pa','delta_floor_pa','initial_dead_pressure_pa',
             'upstream_pressure_correction_pa','downstream_pressure_correction_pa')}
        self.base_pressure_labels={name:label.text() for name,label in self.pressure_labels.items()}
        for name in ('upstream_pressure_correction_pa','downstream_pressure_correction_pa'):
            self.controls[name].setToolTip('Add only a known sensor calibration/zero correction. Applied to the plateau means and every transient sample; raw recording remains unchanged. Do not fit an offset merely to make absolute pressure positive.')
        volumes = QLabel('Connected volumes exclude core pore volume. With unchanged calibration boundaries: '
                         'upstream = VU + DU; downstream = VD + DD. Review valve topology. '
                         'Defaults above are teaching values, not measurements.')
        volumes.setWordWrap(True)
        left.addWidget(volumes)
        self.storage_note=QLabel()
        self.storage_note.setWordWrap(True)
        left.addWidget(self.storage_note)
        self.volume_button = QPushButton('Use linked calibration: VU+DU / VD+DD')
        self.volume_button.clicked.connect(self.use_calibration)
        left.addWidget(self.volume_button)
        times = snapshot.data.Time.to_numpy(float)
        end = float(times[pulse.decay_end_row]-pulse.valve_time_s)
        fit_start = pulse.fit_start_s-pulse.valve_time_s
        fit_end = pulse.fit_end_s-pulse.valve_time_s
        timing = QFormLayout()
        for name, label, value in [
            ('fit_start_s', 'FDM fit start (s from valve)', fit_start),
            ('fit_end_s', 'FDM fit end (s from valve)', fit_end),
            ('late_start_s', 'Tail start (s from valve)', fit_start+.6*(fit_end-fit_start)),
            ('late_end_s', 'Tail end (s from valve)', fit_end),
        ]:
            spin = number(0, max(1,end), value, 6)
            self.controls[name] = spin
            timing.addRow(label, spin)
        left.addLayout(timing)
        self.confirm = QCheckBox('I have checked these sample, gas and volume inputs')
        left.addWidget(self.confirm)
        self.porosity_button = QPushButton('Boyle porosity…')
        self.porosity_button.clicked.connect(self.estimate_porosity)
        self.late_button = QPushButton('Late-time estimate')
        self.fit_button = QPushButton('FDM history match')
        self.fit_button.setToolTip('Fit permeability with the entered slip factor b held fixed.')
        self.export_button = QPushButton('Export result…')
        self.klinkenberg_button = QPushButton('Klinkenberg…')
        self.klinkenberg_button.setToolTip('Estimate intrinsic permeability and slip from saved late-time fits for the same sample.')
        self.cancel_button = QPushButton('Cancel calculation')
        self.cancel_button.setEnabled(False)
        self.late_button.clicked.connect(lambda: self.start('late'))
        self.fit_button.clicked.connect(lambda: self.start('fdm'))
        self.export_button.clicked.connect(self.export_result)
        self.klinkenberg_button.clicked.connect(self.klinkenberg)
        self.cancel_button.clicked.connect(self.cancel)
        actions = QHBoxLayout()
        for button in (self.porosity_button,self.late_button,self.fit_button,self.export_button,self.klinkenberg_button,self.cancel_button):
            actions.addWidget(button)
        left.addStretch()
        split.addWidget(scroll)
        right = QWidget()
        plots = QVBoxLayout(right)
        self.pressure_plot = pg.PlotWidget(title='Measured and simulated reservoir pressures')
        self.tail_plot = pg.PlotWidget(title='Drag the shaded interval to choose the late-time tail')
        self.residual_plot = pg.PlotWidget(title='FDM residuals: simulated − measured')
        for plot, ylabel, units in ((self.pressure_plot,'Absolute pressure','kPa'),
                                    (self.tail_plot,'ln(ΔP / initial ΔP)',None),
                                    (self.residual_plot,'Pressure residual','kPa')):
            plot.setLabel('bottom','Time from valve opening',units='s')
            plot.setLabel('left',ylabel,units=units)
            plot.getAxis('bottom').enableAutoSIPrefix(False)
            plot.getAxis('left').enableAutoSIPrefix(False)
            plot.showGrid(x=True,y=True,alpha=.2)
            plot.addLegend()
            plots.addWidget(plot, 2)
        self.tail_plot.setXLink(self.pressure_plot)
        self.residual_plot.setXLink(self.pressure_plot)
        self.summary = QTextBrowser()
        self.summary.setMinimumHeight(160)
        plots.addWidget(self.summary, 2)
        split.addWidget(right)
        split.setSizes([400, 1000])
        root.addLayout(actions)
        self.status = QLabel('Choose pressure channels. Defaults must be checked before calculation.')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('QLabel { background: #fff4d6; color: #3b2b10; padding: 6px; }')
        root.insertWidget(1, self.status)
        saved = self.project.analysis_settings.get(pulse.uid)
        if saved:
            # Do not silently reinterpret old sessions as a different valve setup.
            self.set_settings({'initial_upstream_mode':'Combined storage','fdm_backend':'Reference explicit',**saved})
        for name, control in self.controls.items():
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self.inputs_changed)
            else:
                control.valueChanged.connect(self.inputs_changed)
                # textEdited is user-only, unlike valueChanged. An explicit
                # entry must replace a hidden full-precision saved value even
                # when the new text equals its rounded display value.
                control.lineEdit().textEdited.connect(
                    lambda _text, key=name: self.numeric_input_edited(key))
        main_view=controller.main.view
        self.display_units.currentTextChanged.connect(main_view.pressure_units.setCurrentText)
        main_view.pressure_units.currentTextChanged.connect(self.set_pressure_unit)
        self.recording_basis.currentTextChanged.connect(controller.view.pressure_basis.setCurrentText)
        self.barometric.valueChanged.connect(lambda _:controller.view.barometric.set_canonical_value(self.barometric.canonical_value()))
        controller.view.pressure_basis.currentTextChanged.connect(self.recording_setup_changed)
        controller.view.barometric.valueChanged.connect(self.recording_setup_changed)
        self.set_pressure_unit(controller.main.model.pressure_display_unit)
        stored = self.project.analysis_results.get(pulse.uid, {})
        for method in ('fdm','late'):
            record = stored.get(method)
            if record:
                try:
                    settings = self.settings()
                    if record['fingerprint'] == analysis_fingerprint(self.project,pulse,settings):
                        self.display(method,record['result'])
                        break
                except (ValueError, KeyError):
                    pass
        # Keep the complete dialog, including the fixed action row, on screen.
        self.move(available.center()-self.rect().center())

    def settings(self):
        values = {key:(c.currentData() if isinstance(c,QComboBox) else c.value())
                  for key,c in self.controls.items()}
        for key,factor in self.unit_factors().items():
            values[key] *= factor
        # Spin boxes have display precision. Preserve exact saved SI values
        # while their displayed controls are unchanged, so reopening a valid
        # saved fit does not invalidate it through harmless unit-rounding.
        for key,value in getattr(self,'restored_settings',{}).items():
            control = self.controls.get(key)
            if control is None:
                values[key] = value
            elif not isinstance(control,QComboBox):
                shown = value/self.unit_factors().get(key,1)
                if control.value() == round(shown,control.decimals()):
                    values[key] = value
        values['cells'] = int(values['cells'])
        values['max_steps'] = int(values['max_steps'])
        return AnalysisSettings(**values)

    def numeric_input_edited(self, key):
        self.restored_settings.pop(key, None)
        self.inputs_changed()

    def unit_factors(self):
        pressure=PRESSURE_UNITS[self.pressure_unit]
        return dict(length_m=1e-3,diameter_m=1e-3,upstream_volume_m3=1e-6,
                    downstream_volume_m3=1e-6,upstream_dead_volume_m3=1e-6,
                    initial_dead_pressure_pa=pressure,viscosity_pa_s=1e-6,slip_pa=pressure,
                    initial_k_m2=MICRODARCY_M2,min_k_m2=MICRODARCY_M2,max_k_m2=MICRODARCY_M2,
                    sigma_up_pa=pressure,sigma_down_pa=pressure,delta_floor_pa=pressure,
                    upstream_pressure_correction_pa=pressure,downstream_pressure_correction_pa=pressure)

    def set_pressure_unit(self,unit):
        settings=asdict(self.settings())
        previous=self.result
        old_factor=PRESSURE_UNITS[self.pressure_unit]
        new_factor=PRESSURE_UNITS[unit]
        for name in self.pressure_labels:
            spin=self.controls[name];blocked=spin.blockSignals(True)
            spin.setRange(spin.minimum()*old_factor/new_factor,spin.maximum()*old_factor/new_factor)
            spin.setValue(settings[name]/new_factor)
            spin.blockSignals(blocked)
            self.pressure_labels[name].setText(self.base_pressure_labels[name].replace('kPa',unit))
        self.pressure_unit=unit
        self.restored_settings=settings
        self.barometric.set_pressure_unit(unit)
        blocked=self.display_units.blockSignals(True);self.display_units.setCurrentText(unit);self.display_units.blockSignals(blocked)
        self.pressure_plot.setLabel('left','Absolute pressure',units=unit)
        self.residual_plot.setLabel('left','Pressure residual',units=unit)
        if previous:self.display(previous[0],previous[1])
        else:self.redraw()

    def recording_setup_changed(self,*_):
        blocked=self.recording_basis.blockSignals(True)
        self.recording_basis.setCurrentText(self.project.pressure_basis)
        self.recording_basis.blockSignals(blocked)
        blocked=self.barometric.blockSignals(True)
        self.barometric.set_canonical_value(self.project.barometric_kpa or 0)
        self.barometric.blockSignals(blocked)
        self.update_atmospheric_readout()
        self.inputs_changed()

    def update_atmospheric_readout(self):
        bar=self.project.barometric_kpa
        text='Atmospheric reference not entered.' if bar is None else (
            f'Atmospheric reference: {bar:.7g} kPa abs = {bar*1000/PRESSURE_UNITS["psi"]:.7g} psia. '
            'Enter the measured local atmospheric pressure in the selected input unit.')
        self.atmospheric_readout.setText(text)

    def pressure_text(self,value,absolute=False):
        return pressure_text(value,self.pressure_unit,absolute=absolute)

    def set_settings(self, values):
        self.restored_settings = dict(values)
        for key,c in self.controls.items():
            if key not in values:
                continue
            if isinstance(c,QComboBox):
                c.setCurrentIndex(max(0,c.findData(values[key])))
            else:
                c.setValue(values[key]/self.unit_factors().get(key,1))

    def use_calibration(self):
        v = self.project.calibrated_volumes_cm3
        if not all(key in v for key in ('VU','VD','DU','DD')):
            QMessageBox.information(self,'No linked calibration','First link the current calibration in the Pulse picker, or enter connected volumes explicitly.')
            return
        self.controls['upstream_volume_m3'].setValue(v['VU']+v['DU'])
        self.controls['downstream_volume_m3'].setValue(v['VD']+v['DD'])
        self.controls['upstream_dead_volume_m3'].setValue(v['DU'])

    def estimate_porosity(self):
        from .porosity_view import PorosityDialog
        dialog = PorosityDialog(self)
        dialog.exec()

    def inputs_changed(self, *_):
        if self.syncing:
            return
        self.result = None
        self.summary.setPlainText('Inputs changed. Run the calculation to obtain a result for these inputs.')
        self.redraw()

    def redraw(self):
        adaptive=self.controls['fdm_backend'].currentData()=='Adaptive BDF'
        self.budget_label.setText('Maximum BDF RHS evaluations' if adaptive else 'Maximum FDM steps per solve')
        self.controls['safety_factor'].setEnabled(not adaptive and not (self.worker and self.worker.isRunning()))
        self.controls['safety_factor'].setToolTip('Used only by the reference explicit solver. BDF controls time accuracy with fixed rtol=1e-8 and pressure atol=0.01 Pa.')
        isolated=self.controls['initial_upstream_mode'].currentData()=='Isolated tank'
        entered=self.controls['dead_pressure_mode'].currentData()=='Entered absolute pressure'
        busy=self.worker is not None and self.worker.isRunning()
        self.controls['upstream_dead_volume_m3'].setEnabled(isolated and not busy)
        self.controls['dead_pressure_mode'].setEnabled(isolated and not busy)
        self.controls['initial_dead_pressure_pa'].setEnabled(isolated and entered and not busy)
        charged=self.controls['upstream_volume_m3'].value()-self.controls['upstream_dead_volume_m3'].value()
        self.storage_note.setText(
            f'Isolated tank: charged VU = (VU+DU) − DU = {charged:.6g} cm³. '
            'Boyle balance uses the pre-opening tank plateau. DU uses core/proxy temperature. '
            'FDM approximates rapid isothermal VU–DU mixing at valve t=0; select a fit start after valve artifacts.'
            if isolated else
            'Combined storage (legacy assumption): all VU+DU starts at the upstream plateau. '
            'For a charged VU isolated from DU by the valve, select Isolated tank and load/enter DU.')
        for plot in (self.pressure_plot,self.tail_plot,self.residual_plot):
            plot.clear()
            plot.plotItem.legend.clear()
        try:
            settings = self.settings()
            trace = extract_trace(self.project,self.pulse,settings,preview=True)
        except ValueError as exc:
            self.status.setText(str(exc))
            self.summary.setPlainText('Preview cannot be drawn:\n'+str(exc))
            return
        t = trace.time_s
        for values,color,name in ((trace.upstream_pa,'#35a7ff','Upstream measured'),
                                  (trace.downstream_pa,'#f6b44c','Downstream measured')):
            self.pressure_plot.plot(t,values/PRESSURE_UNITS[self.pressure_unit],pen=pg.mkPen(color,width=1.5),name=name,connect='finite')
        dp = trace.upstream_pa-trace.downstream_pa
        mask = np.isfinite(dp)&(dp>0)
        self.tail_plot.plot(t[mask],np.log(dp[mask]/(trace.initial_upstream_pa-trace.initial_downstream_pa)),
                            pen=pg.mkPen('#bca7ff'),name='Measured log difference')
        self.tail_region = pg.LinearRegionItem((settings.late_start_s,settings.late_end_s),
                                              brush=(100,180,100,45),bounds=(0,float(t[-1])))
        self.tail_plot.addItem(self.tail_region)
        self.tail_region.sigRegionChangeFinished.connect(self.region_changed)
        fit_region = pg.LinearRegionItem((settings.fit_start_s,settings.fit_end_s),
                                         movable=False,brush=(100,150,200,25))
        self.pressure_plot.addItem(fit_region)
        self.residual_plot.addItem(pg.InfiniteLine(pos=0,angle=0,pen=pg.mkPen('#999')))
        self.status.setText('Shaded pressure interval = FDM observations; shaded semilog interval = late-time fit. FDM always starts at valve t=0.')
        if trace.pressure_error:
            self.status.setText('Preview only — '+trace.pressure_error)

    def region_changed(self):
        start,end = self.tail_region.getRegion()
        self.syncing = True
        self.controls['late_start_s'].setValue(start)
        self.controls['late_end_s'].setValue(end)
        self.syncing = False
        self.inputs_changed()

    def start(self, method):
        if self.worker and self.worker.isRunning():
            return
        if not self.confirm.isChecked():
            QMessageBox.information(self,'Check physical inputs','Check the measured sample, gas and connected-volume inputs, then tick the confirmation box.')
            return
        try:
            settings = self.settings()
            trace = extract_trace(self.project,self.pulse,settings)
        except ValueError as exc:
            QMessageBox.warning(self,'Cannot analyze pulse',str(exc))
            return
        self.running_settings = settings
        self.running_fingerprint = analysis_fingerprint(self.project,self.pulse,settings)
        self.pending_result = None
        self.running_method = method
        self.set_busy(True)
        self.worker = AnalysisWorker(trace,settings,method,self)
        self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(self.calculation_complete)
        self.worker.failed.connect(self.calculation_failed)
        self.worker.finished.connect(self.worker_finished)
        self.status.setText('Calculating…')
        self.worker.start()

    def set_busy(self,busy):
        for c in self.controls.values():
            c.setEnabled(not busy)
        self.controls['safety_factor'].setEnabled(not busy and self.controls['fdm_backend'].currentData()=='Reference explicit')
        for button in (self.porosity_button,self.late_button,self.fit_button,self.export_button,self.klinkenberg_button,self.volume_button):
            button.setEnabled(not busy)
        self.confirm.setEnabled(not busy)
        self.recording_basis.setEnabled(not busy);self.barometric.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if hasattr(self,'tail_region'):
            self.tail_region.setMovable(not busy)

    def calculation_complete(self,result):
        if not self.worker.cancel.is_set():
            self.pending_result = result

    def calculation_failed(self,message):
        self.status.setText(message)
        self.summary.setPlainText(message)

    def worker_finished(self):
        self.set_busy(False)
        if self.worker.cancel.is_set():
            self.status.setText('Cancelled. No new result saved.')
            self.pending_result = None
        if self.pending_result is not None:
            method,result = self.running_method,self.pending_result
            self.project.analysis_settings[self.pulse.uid] = asdict(self.running_settings)
            self.project.analysis_results.setdefault(self.pulse.uid,{})[method] = dict(
                fingerprint=self.running_fingerprint,result=result,settings=asdict(self.running_settings))
            self.controller.changed()
            self.display(method,result)
        self.worker.deleteLater()
        self.worker = None

    def display(self,method,result):
        self.redraw()
        self.result = (method,result,analysis_fingerprint(self.project,self.pulse,self.settings()))
        if method == 'late':
            self.tail_plot.plot(result['time_s'],result['predicted_log_delta'],
                                pen=pg.mkPen('#ff6868',width=3),name='Late-time regression')
            text = (f"Dicker–Smits apparent k: {result['k_apparent_microdarcy']:.6g} µD\n"
                    f"Brace (negligible core storage): {result['brace_microdarcy']:.6g} µD\n"
                    f"R²: {result['r_squared']:.6g}; slope: {result['slope_per_s']:.6g} s⁻¹; "
                    f"n={result['samples']}; mean pressure={self.pressure_text(result['mean_absolute_pa'],True)}\n"
                    f"First eigenvalue: {result['theta_1']:.6g}; storage factor: {result['storage_factor']:.6g}\n"
                    'This is apparent gas permeability; intrinsic k needs slip correction across pressures.\n')
        else:
            t = result['time_s']
            for key,color,name in [('simulated_upstream_pa','#35a7ff','Upstream FDM'),
                                   ('simulated_downstream_pa','#f6b44c','Downstream FDM')]:
                self.pressure_plot.plot(t,np.array(result[key])/PRESSURE_UNITS[self.pressure_unit],
                    pen=pg.mkPen(color,width=3,style=Qt.PenStyle.DashLine),name=name)
            for key,color,name in [('upstream_residual_pa','#35a7ff','Upstream'),
                                   ('downstream_residual_pa','#f6b44c','Downstream')]:
                a = np.array([np.nan if v is None else v for v in result[key]])
                self.residual_plot.plot(t,a/PRESSURE_UNITS[self.pressure_unit],pen=pg.mkPen(color),name=name,connect='finite')
            kind = 'Apparent k (b=0 model)' if result['fixed_slip_pa']==0 else 'Intrinsic k conditional on entered b'
            timestep='adaptive' if result['dt_s'] is None else f"{result['dt_s']:.5g} s"
            text = (f"{kind}: {result['fitted_k_microdarcy']:.6g} µD\n"
                    f"Time integrator: {result.get('fdm_backend','Reference explicit')}\n"
                    f"Fixed b: {self.pressure_text(result['fixed_slip_pa'])}; "
                    f"k_app at mean pressure: {result['apparent_at_mean_microdarcy']:.6g} µD\n"
                    f"RMSE upstream/downstream: {self.pressure_text(result['rmse_upstream_pa'])} / "
                    f"{self.pressure_text(result['rmse_downstream_pa'])}\n"
                    f"Continuous predicted equilibrium: {self.pressure_text(result['predicted_continuous_equilibrium_pa'],True)}; "
                    f"selected final mean: {self.pressure_text(result['observed_final_mean_pa'],True)}\n"
                    f"N={self.settings().cells}; dt={timestep}; steps={result['steps']:,}; "
                    f"max inventory drift={result['max_inventory_error']:.3g}\n")
            diagnostics=result.get('adaptive_diagnostics',{})
            if diagnostics:
                text+=(f"BDF RHS calls={diagnostics['nfev']:,}; rtol={diagnostics['rtol']:g}; "
                       f"pressure atol={diagnostics['atol_pa']:g} Pa\n")
            state=result.get('initial_storage')
            if state:
                text+=(f"Initial arrangement: {state['mode']}; pre-opening tank plateau "
                       f"{self.pressure_text(state['tank_plateau_pa'],True)}; "
                       f"FDM mixed upstream {self.pressure_text(state['mixed_upstream_pa'],True)}\n")
        self.summary.setPlainText(text+'\nReview:\n'+'\n'.join('• '+w for w in result['warnings']))
        self.status.setText('Result retained in memory. Use Save pulses to persist the session, or export this result.')

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText('Cancelling after the current bounded forward solve…')

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.cancel()
            event.ignore()
        else:
            event.accept()

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.cancel()
        else:
            super().reject()

    def export_result(self):
        if self.result is None:
            QMessageBox.information(self,'No current result','Run an analysis with the current inputs first.')
            return
        method,result,fingerprint = self.result
        settings = self.settings()
        if fingerprint != analysis_fingerprint(self.project,self.pulse,settings):
            QMessageBox.warning(self,'Result is stale','Inputs changed. Recalculate before exporting.')
            return
        filename,_ = QFileDialog.getSaveFileName(self,'Export permeability result','permeability_result.json','JSON (*.json)')
        if not filename:
            return
        trace = extract_trace(self.project,self.pulse,settings)
        snapshot = self.project.snapshots[self.pulse.dataset_id]
        payload = dict(format='pulse-permeability-result',schema_version=1,pulse_uid=self.pulse.uid,
            pulse_label=self.pulse.label,sample_id=self.pulse.sample_id,dataset_id=self.pulse.dataset_id,
            source_sha256=snapshot.source_sha256,fingerprint=fingerprint,settings=asdict(settings),
            method=method,result=result,units='SI unless field explicitly says microdarcy',
            measured=dict(time_s=trace.time_s.tolist(),upstream_pa=_finite_list(trace.upstream_pa),
                          downstream_pa=_finite_list(trace.downstream_pa)))
        path = Path(filename).with_suffix('.json')
        from .pulse_capture_io import protect
        try:
            protect(self.project,path)
            if self.controller.session_path and path.resolve() == self.controller.session_path.resolve():
                raise ValueError('Choose a separate result file; the open pulse session cannot be overwritten by an analysis export.')
            path.write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
        except Exception as exc:
            QMessageBox.warning(self,'Could not export result',str(exc))
            return
        self.status.setText(f'Exported {path}')

    def klinkenberg(self):
        if not self.pulse.sample_id.strip():
            QMessageBox.information(self,'Set a Sample ID',
                'Enter the same nonempty Sample ID on comparable captured pulses, save the edits, and calculate a late-time fit for each.')
            return
        from .klinkenberg_view import KlinkenbergDialog
        dialog = KlinkenbergDialog(self)
        dialog.exec()
