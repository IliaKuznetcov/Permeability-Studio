"""Review a gas-balance porosity estimate before applying it to permeability analysis."""
from dataclasses import asdict
import json
from pathlib import Path
from PySide6.QtWidgets import (
    QCheckBox,QComboBox,QDialog,QFileDialog,QFormLayout,QHBoxLayout,QLabel,
    QMessageBox,QPushButton,QScrollArea,QTextBrowser,QVBoxLayout,QWidget,
)
from .calibration_view import number
from .pressure_widgets import PressureSpinBox
from .porosity import PorosityOptions,porosity_from_pulse,porosity_fingerprint


class PorosityDialog(QDialog):
    def __init__(self,analysis):
        super().__init__(analysis)
        self.analysis=analysis
        self.project,self.pulse=analysis.project,analysis.pulse
        self.settings=analysis.settings()
        self.result=None
        self.setWindowTitle('Boyle / gas-balance porosity — '+self.pulse.label)
        area=self.screen().availableGeometry()
        self.resize(min(950,int(area.width()*.94)),min(850,int(area.height()*.9)))
        root=QVBoxLayout(self)
        explanation=QLabel('Estimate connected pore volume from the captured initial/final states. '
            'Connected tank volumes are those currently entered in the analysis window (excluding core pores). '
            'Use the stabilized tank plateau before valve opening, the initial downstream plateau, and the final equalized plateau. '
            'The initial arrangement and DU pressure come from the analysis window. '
            'The initial core pressure is assumed to equal the initial downstream pressure; DU uses core/proxy temperature.')
        explanation.setWordWrap(True);root.addWidget(explanation)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        panel=QWidget();scroll.setWidget(panel);layout=QVBoxLayout(panel)
        root.addWidget(scroll,1)
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.display_units=QComboBox();self.display_units.addItems(['kPa','psi'])
        self.display_units.setCurrentText(analysis.pressure_unit)
        form.addRow('Pressure display',self.display_units)
        self.mode=QComboBox();self.mode.addItems(['Common temperature','Captured temperatures'])
        self.eos=QComboBox();self.eos.addItems(['Ideal gas (Z=1)','Peng–Robinson nitrogen'])
        self.common=number(-273.149999,1000,25,6)
        form.addRow('Gas model',self.eos)
        form.addRow('Temperature treatment',self.mode)
        form.addRow('Common temperature (°C)',self.common)
        self.temperatures={}
        channels=[c for c in self.project.snapshots[self.pulse.dataset_id].channels if c.quantity=='Temperature']
        for role,label,location in [('upstream','Upstream temperature','upstream'),
                                    ('downstream','Downstream temperature','downstream'),
                                    ('core','Core temperature or explicit proxy','core')]:
            combo=QComboBox();combo.addItem('Choose sensor…','')
            if role=='core':
                combo.addItem('Use downstream sensor as core-temperature proxy','__downstream__')
            for c in channels:
                combo.addItem(f'{c.source_column} — {c.location}',c.key)
            matches=[c for c in channels if c.location.lower()==location]
            if len(matches)==1:
                combo.setCurrentIndex(combo.findData(matches[0].key))
            elif role=='core':
                combo.setCurrentIndex(combo.findData('__downstream__'))
            self.temperatures[role]=combo;form.addRow(label,combo)
        if all(self.temperatures[k].currentData() for k in ('upstream','downstream')):
            self.mode.setCurrentText('Captured temperatures')
        self.mismatch=PressureSpinBox(.000001,10000,1,6,qualifier='')
        self.drift=PressureSpinBox(.000001,10000,1,6,qualifier='/min')
        form.addRow('Maximum final Pu−Pd mismatch (kPa)',self.mismatch)
        form.addRow('Maximum final pressure drift (kPa/min)',self.drift)
        layout.addLayout(form)
        self.core_confirm=QCheckBox('Core, downstream chamber and downstream tank were initially equilibrated at measured downstream pressure.')
        self.closed_confirm=QCheckBox('Final windows represent equilibrium; no gas was added, vented or lost during the pulse.')
        layout.addWidget(self.core_confirm);layout.addWidget(self.closed_confirm)
        self.status=QLabel('Review the assumptions and temperature mappings, then calculate.')
        self.status.setWordWrap(True);self.status.setStyleSheet('background:#fff4d6;color:#3b2b10;padding:6px;')
        layout.addWidget(self.status)
        self.report=QTextBrowser();self.report.setMinimumHeight(200);layout.addWidget(self.report,1)
        buttons=QHBoxLayout()
        self.calculate_button=QPushButton('Calculate porosity')
        self.apply_button=QPushButton('Use this porosity in analysis')
        self.export_button=QPushButton('Export estimate…')
        self.close_button=QPushButton('Close')
        for b in (self.calculate_button,self.apply_button,self.export_button,self.close_button):buttons.addWidget(b)
        root.addLayout(buttons)
        self.calculate_button.clicked.connect(self.calculate)
        self.apply_button.clicked.connect(self.apply)
        self.export_button.clicked.connect(self.export)
        self.close_button.clicked.connect(self.accept)
        saved=self.project.analysis_results.get(self.pulse.uid,{}).get('porosity')
        if saved:
            o=PorosityOptions(**saved['options'])
            self.mode.setCurrentText(o.temperature_mode);self.eos.setCurrentText(o.eos)
            self.common.setValue(o.common_temperature_c)
            self.mismatch.set_canonical_value(o.final_pressure_tolerance_pa/1000)
            self.drift.set_canonical_value(o.final_drift_limit_pa_min/1000)
            for role,key in [('upstream',o.upstream_temperature_key),('downstream',o.downstream_temperature_key),('core',o.core_temperature_key)]:
                self.temperatures[role].setCurrentIndex(max(0,self.temperatures[role].findData(key)))
        self.limit_labels=(form.labelForField(self.mismatch),form.labelForField(self.drift))
        main_units=analysis.controller.main.view.pressure_units
        self.display_units.currentTextChanged.connect(main_units.setCurrentText)
        main_units.currentTextChanged.connect(self.set_pressure_unit)
        self.set_pressure_unit(analysis.pressure_unit)
        for c in (self.mode,self.eos,*self.temperatures.values()):c.currentIndexChanged.connect(self.invalidate)
        for c in (self.common,self.mismatch,self.drift):c.valueChanged.connect(self.invalidate)
        self.core_confirm.toggled.connect(self.invalidate);self.closed_confirm.toggled.connect(self.invalidate)
        self.invalidate()
        if saved and saved['fingerprint']==porosity_fingerprint(self.project,self.pulse,self.settings,self.options()):
            self.display(saved['result'])
            self.status.setText('Saved estimate shown. Confirm the assumptions and recalculate before applying it.')
        self.move(area.center()-self.rect().center())

    def options(self):
        return PorosityOptions(temperature_mode=self.mode.currentText(),common_temperature_c=self.common.value(),
            upstream_temperature_key=self.temperatures['upstream'].currentData(),
            downstream_temperature_key=self.temperatures['downstream'].currentData(),
            core_temperature_key=self.temperatures['core'].currentData(),eos=self.eos.currentText(),
            final_pressure_tolerance_pa=self.mismatch.canonical_value()*1000,
            final_drift_limit_pa_min=self.drift.canonical_value()*1000)

    def set_pressure_unit(self,unit):
        self.mismatch.set_pressure_unit(unit);self.drift.set_pressure_unit(unit)
        self.limit_labels[0].setText(f'Maximum final Pu−Pd mismatch ({unit})')
        self.limit_labels[1].setText(f'Maximum final pressure drift ({unit}/min)')
        blocked=self.display_units.blockSignals(True);self.display_units.setCurrentText(unit);self.display_units.blockSignals(blocked)
        if self.result:self.display(self.result)

    def invalidate(self,*_):
        captured=self.mode.currentText()=='Captured temperatures'
        for c in self.temperatures.values():c.setEnabled(captured)
        self.common.setEnabled(not captured)
        self.result=None;self.apply_button.setEnabled(False);self.export_button.setEnabled(False)
        self.report.clear()
        self.status.setText('Inputs changed. Review assumptions and calculate the estimate.')

    def confirmed(self):
        return self.core_confirm.isChecked() and self.closed_confirm.isChecked()

    def calculate(self):
        if not self.confirmed():
            self.status.setText('Confirm the initial core condition and closed-system final equilibrium first.')
            return
        try:
            o=self.options()
            result=porosity_from_pulse(self.project,self.pulse,self.settings,o)
            fingerprint=porosity_fingerprint(self.project,self.pulse,self.settings,o)
        except ValueError as exc:
            self.result=None;self.apply_button.setEnabled(False);self.export_button.setEnabled(False)
            self.status.setText(str(exc));self.report.setPlainText(str(exc));return
        self.project.analysis_results.setdefault(self.pulse.uid,{})['porosity']=dict(
            fingerprint=fingerprint,options=asdict(o),result=result)
        # Retain the reviewed topology/volume inputs even if phi is not applied.
        self.project.analysis_settings[self.pulse.uid]=asdict(self.settings)
        self.analysis.controller.changed()
        self.display(result)
        self.status.setText('Estimate retained in the pulse session. Apply it explicitly to change the permeability input.')

    def display(self,result):
        self.result=result
        self.apply_button.setEnabled(True);self.export_button.setEnabled(True)
        text=(f"Connected gas-accessible porosity: {result['porosity_percent']:.5g}% "
              f"(fraction {result['porosity_fraction']:.7g})\n"
              f"Pore volume: {result['pore_volume_cm3']:.7g} cm³; bulk volume: {result['bulk_volume_cm3']:.7g} cm³\n"
              f"Gas model: {result['eos']}; temperatures: {result['temperature_mode']}\n"
              f"Initial core pressure = downstream initial: {self.analysis.pressure_text(result['initial_core_pressure_pa'],True)}\n"
              f"Final common pressure: {self.analysis.pressure_text(result['final_common_pressure_pa'],True)}; "
              f"sensor mismatch: {self.analysis.pressure_text(result['final_pressure_mismatch_pa'])}\n\n")
        state=result.get('initial_storage')
        if state:
            text+=(f"Initial arrangement: {state['mode']}\n"
                   f"Precharged upstream group: {result['charged_upstream_volume_cm3']:.7g} cm³; "
                   f"DU at separate initial pressure: {result['upstream_dead_volume_cm3']:.7g} cm³\n"
                   f"Pre-opening tank plateau: {self.analysis.pressure_text(state['tank_plateau_pa'],True)}; "
                   f"downstream initial: {self.analysis.pressure_text(result['pressures_pa']['di'],True)}\n")
            if state['dead_volume_m3']>0:
                text+=f"Initial DU pressure: {self.analysis.pressure_text(state['initial_dead_pressure_pa'],True)} ({state['dead_pressure_mode']}); DU temperature uses core/proxy sensor.\n"
        for k,label in [('ui','Upstream initial'),('di','Downstream initial'),('ci','Core initial'),
                        ('uf','Upstream final'),('df','Downstream final'),('cf','Core final'),
                        ('dui','DU initial'),('duf','DU final')]:
            if k not in result['temperatures_k']:continue
            text+=f"{label}: T={result['temperatures_k'][k]-273.15:.5g} °C; Z={result['z_factors'][k]:.6g}\n"
        text+='\nReview:\n'+'\n'.join('• '+w for w in result['warnings'])
        text+='\n\nUsing this estimate to close gas balance is not an independent validation of volumes, initial core pressure or equilibrium. The FDM remains an isothermal ideal-gas model.'
        self.report.setPlainText(text)

    def current_result(self):
        entry=self.project.analysis_results.get(self.pulse.uid,{}).get('porosity')
        return (self.result is not None and entry is not None and
                entry['fingerprint']==porosity_fingerprint(self.project,self.pulse,self.settings,self.options()))

    def apply(self):
        if not self.confirmed() or not self.current_result():
            self.status.setText('Confirm both assumptions and calculate a current estimate before applying it.');return
        phi=self.result['porosity_fraction']
        control=self.analysis.controls['porosity']
        if not control.minimum()<=round(phi,control.decimals())<=control.maximum():
            self.status.setText('Estimate is outside the permeability control range. Export and review it before applying.');return
        # Preserve the full precision behind the displayed spin-box value.
        self.analysis.restored_settings=getattr(self.analysis,'restored_settings',{})|{'porosity':phi}
        self.analysis.controls['porosity'].setValue(phi)
        self.analysis.inputs_changed()
        self.project.analysis_settings[self.pulse.uid]=asdict(self.analysis.settings())
        self.analysis.controller.changed()
        self.analysis.status.setText(f'Applied gas-balance porosity {phi*100:.5g}%. Recalculate late-time and FDM fits with this input.')
        self.accept()

    def export(self):
        if not self.current_result():
            self.status.setText('Calculate a current estimate before exporting.');return
        filename,_=QFileDialog.getSaveFileName(self,'Export porosity estimate','porosity_estimate.json','JSON (*.json)')
        if not filename:return
        path=Path(filename).with_suffix('.json')
        from .pulse_capture_io import protect
        try:
            protect(self.project,path)
            if self.analysis.controller.session_path and path.resolve()==self.analysis.controller.session_path.resolve():
                raise ValueError('Choose a separate result file, not the open pulse session.')
            payload=dict(format='pulse-porosity-result',schema_version=1,pulse_uid=self.pulse.uid,
                sample_id=self.pulse.sample_id,dataset_id=self.pulse.dataset_id,
                analysis=self.project.analysis_results[self.pulse.uid]['porosity'])
            path.write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
        except Exception as exc:
            QMessageBox.warning(self,'Could not export estimate',str(exc));return
        self.status.setText(f'Exported {path}')
