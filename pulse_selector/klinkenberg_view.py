"""Review comparable saved late-time estimates before Klinkenberg regression."""
from dataclasses import asdict
from math import isclose
import json
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox,QComboBox,QDialog,QFileDialog,QHBoxLayout,QHeaderView,QLabel,QMessageBox,QPushButton,QTableWidget,QTableWidgetItem,QTextBrowser,QVBoxLayout
from .permeability import AnalysisSettings,MICRODARCY_M2,analysis_fingerprint,fit_klinkenberg
from .models import PRESSURE_UNITS


SAMPLE_INPUTS = (
    ('length_m', 'core length', 1e3, 'mm'),
    ('diameter_m', 'core diameter', 1e3, 'mm'),
    ('porosity', 'porosity', 1, 'fraction'),
    ('upstream_volume_m3', 'connected upstream volume', 1e6, 'cm³'),
    ('downstream_volume_m3', 'connected downstream volume', 1e6, 'cm³'),
    ('viscosity_pa_s', 'gas viscosity', 1e6, 'µPa·s'),
)


def check_sample_inputs(reference, candidate, reference_label, candidate_label):
    """Report real input differences; allow only floating-point roundoff."""
    differences = []
    for key, label, factor, unit in SAMPLE_INPUTS:
        first, second = getattr(reference, key), getattr(candidate, key)
        if not isclose(first, second, rel_tol=1e-12, abs_tol=0):
            differences.append(
                f'{label}: {reference_label} = {first*factor:.15g} {unit}; '
                f'{candidate_label} = {second*factor:.15g} {unit}')
    if differences:
        raise ValueError('Different sample inputs. ' + ' | '.join(differences) +
            '. Enter common reviewed inputs and recalculate the affected late-time fit. '
            'Saved values may have more precision than the displayed controls; '
            'explicitly retyping a value in version 4.5.1 or later replaces it.')


class KlinkenbergDialog(QDialog):
    def __init__(self,analysis):
        super().__init__(analysis)
        self.analysis=analysis;self.project=analysis.project
        self.sample=analysis.pulse.sample_id.strip()
        self.rows=[];self.result=None
        self.setWindowTitle('Klinkenberg regression — '+self.sample)
        area=self.screen().availableGeometry()
        self.resize(min(1100,int(area.width()*.94)),min(900,int(area.height()*.9)))
        root=QVBoxLayout(self)
        label=QLabel('Uses saved Dicker–Smits apparent permeabilities at their tail mean absolute pressures. '
            'Select at least three comparable pulses from this Sample ID. Review tail warnings, gas, temperature '
            'and effective stress. This is not joint nonlinear FDM inversion.')
        label.setWordWrap(True);root.addWidget(label)
        self.display_units=QComboBox();self.display_units.addItems(['kPa','psi'])
        self.display_units.setCurrentText(analysis.pressure_unit);root.addWidget(self.display_units)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(['Use','Pulse','Mean P (kPa abs)','k apparent (µD)','Tail R²','Review'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.blockSignals(True)
        previous=self.project.sample_results.get(self.sample,{}).get('klinkenberg')
        previous_ids=set(previous.get('pulse_uids',[])) if previous else None
        for pulse in self.project.pulses:
            if pulse.sample_id.strip()!=self.sample:continue
            entry=self.project.analysis_results.get(pulse.uid,{}).get('late')
            reason='No saved late-time fit';eligible=False;s=None
            if entry:
                try:
                    s=AnalysisSettings(**entry['settings'])
                    if entry['fingerprint']!=analysis_fingerprint(self.project,pulse,s):
                        reason='Stale: pulse, recording or pressure basis changed'
                    elif entry['result']['r_squared']<.98:
                        reason='Tail R² < 0.98: review and refit'
                    else:
                        eligible=True
                        reason=f"{len(entry['result'].get('warnings',[]))} fit warnings — review original fit"
                except (ValueError,KeyError,TypeError):reason='Unsupported saved fit'
            row=self.table.rowCount();self.table.insertRow(row)
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled if eligible else Qt.ItemFlag.NoItemFlags)
            include=eligible and (previous_ids is None or pulse.uid in previous_ids)
            check.setCheckState(Qt.CheckState.Checked if include else Qt.CheckState.Unchecked)
            self.table.setItem(row,0,check)
            r=entry['result'] if entry else {}
            values=[pulse.label,f"{r['mean_absolute_pa']/1000:.7g}" if eligible else '',
                f"{r['k_apparent_microdarcy']:.7g}" if eligible else '',
                f"{r['r_squared']:.6g}" if eligible else '',reason]
            for col,value in enumerate(values,1):
                item=QTableWidgetItem(value);item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row,col,item)
            self.rows.append((pulse,entry,s,eligible))
        self.table.blockSignals(False);self.table.itemChanged.connect(self.invalidate)
        root.addWidget(self.table,2)
        self.confirm=QCheckBox('Selected tests use the same core/gas and comparable temperature and effective stress.')
        self.confirm.toggled.connect(self.invalidate);root.addWidget(self.confirm)
        self.plot=pg.PlotWidget(title='k apparent versus reciprocal mean absolute pressure')
        self.plot.setLabel('bottom','1 / mean absolute pressure',units='MPa⁻¹')
        self.plot.setLabel('left','Apparent permeability',units='µD')
        for axis in ('bottom','left'):self.plot.getAxis(axis).enableAutoSIPrefix(False)
        self.plot.showGrid(x=True,y=True,alpha=.2);self.plot.addLegend();root.addWidget(self.plot,3)
        self.report=QTextBrowser();self.report.setMinimumHeight(120);root.addWidget(self.report,1)
        self.status=QLabel('Review the points and confirm comparable test conditions, then fit.')
        self.status.setWordWrap(True);root.addWidget(self.status)
        buttons=QHBoxLayout()
        self.fit_button=QPushButton('Fit Klinkenberg line')
        self.export_button=QPushButton('Export regression…')
        self.apply_button=QPushButton('Use b for this FDM fit')
        close=QPushButton('Close')
        for b in (self.fit_button,self.export_button,self.apply_button,close):buttons.addWidget(b)
        root.addLayout(buttons)
        self.fit_button.clicked.connect(self.fit);self.export_button.clicked.connect(self.export)
        self.apply_button.clicked.connect(self.apply_b);close.clicked.connect(self.accept)
        self.export_button.setEnabled(False);self.apply_button.setEnabled(False)
        main_units=analysis.controller.main.view.pressure_units
        self.display_units.currentTextChanged.connect(main_units.setCurrentText)
        main_units.currentTextChanged.connect(self.set_pressure_unit)
        self.set_pressure_unit(analysis.pressure_unit)
        self.move(area.center()-self.rect().center())

    def set_pressure_unit(self,unit):
        table_blocked=self.table.blockSignals(True)
        self.table.horizontalHeaderItem(2).setText(f'Mean P ({"psia" if unit=="psi" else "kPa abs"})')
        for row,(_,entry,_,eligible) in enumerate(self.rows):
            if eligible:self.table.item(row,2).setText(f"{entry['result']['mean_absolute_pa']/PRESSURE_UNITS[unit]:.7g}")
        self.table.blockSignals(table_blocked)
        self.plot.setLabel('bottom','1 / mean absolute pressure',units=f'{unit}⁻¹')
        blocked=self.display_units.blockSignals(True);self.display_units.setCurrentText(unit);self.display_units.blockSignals(blocked)
        if self.result:self.display_result()

    def invalidate(self,*_):
        self.result=None;self.export_button.setEnabled(False);self.apply_button.setEnabled(False)
        self.plot.clear();self.plot.plotItem.legend.clear();self.report.clear()
        self.status.setText('Selection or condition confirmation changed. Refit the line.')

    def fit(self):
        if not self.confirm.isChecked():
            self.status.setText('Confirm comparable sample, gas, temperature and effective stress before fitting.');return
        selected=[];records=[];physical=None;physical_label=None
        try:
            for row,(pulse,entry,s,eligible) in enumerate(self.rows):
                if not eligible or self.table.item(row,0).checkState()!=Qt.CheckState.Checked:continue
                if entry['fingerprint']!=analysis_fingerprint(self.project,pulse,s):
                    raise ValueError(f'{pulse.label}: fit is stale. Recalculate it first.')
                if physical is not None:
                    check_sample_inputs(physical,s,physical_label,pulse.label)
                else:
                    physical=s;physical_label=pulse.label
                records.append(entry['result'])
                selected.append(dict(pulse_uid=pulse.uid,pulse_label=pulse.label,dataset_id=pulse.dataset_id,
                    fit_fingerprint=entry['fingerprint'],settings=entry['settings'],
                    mean_absolute_pa=entry['result']['mean_absolute_pa'],
                    k_apparent_m2=entry['result']['k_apparent_m2'],
                    r_squared=entry['result']['r_squared'],warnings=entry['result']['warnings']))
            current=self.analysis.settings()
            if physical is not None:
                check_sample_inputs(physical,current,physical_label,'current analysis window')
            fit=fit_klinkenberg(records)
        except ValueError as exc:
            self.status.setText(str(exc));return
        self.result=dict(method='Cross-pulse unweighted late-time Klinkenberg regression',sample_id=self.sample,
            pulse_uids=[p['pulse_uid'] for p in selected],points=selected,fit=fit)
        self.project.sample_results.setdefault(self.sample,{})['klinkenberg']=self.result
        self.analysis.controller.changed()
        self.display_result()
        self.export_button.setEnabled(True);self.apply_button.setEnabled(True)
        self.status.setText('Regression retained in memory. Save pulses to persist it, or export this regression.')

    def display_result(self):
        selected=self.result['points'];fit=self.result['fit']
        scale=PRESSURE_UNITS[self.analysis.pressure_unit]
        x=np.array([scale/p['mean_absolute_pa'] for p in selected])
        y=np.array([p['k_apparent_m2']/MICRODARCY_M2 for p in selected])
        self.plot.clear();self.plot.plotItem.legend.clear()
        self.plot.plot(x,y,pen=None,symbol='o',symbolSize=10,symbolBrush='#43a7e5',name='Accepted pulse fits')
        line=np.linspace(0,float(x.max())*1.05,100)
        intercept=fit['intrinsic_microdarcy'];slope=intercept*fit['slip_pa']/scale
        self.plot.plot(line,intercept+slope*line,pen=pg.mkPen('#eaaa4c',width=2),name='Klinkenberg regression')
        self.report.setPlainText(f"Intrinsic k∞ = {intercept:.7g} µD\nSlip coefficient b = {self.analysis.pressure_text(fit['slip_pa'])}\n"
            f"n = {fit['samples']}; R² = {fit['r_squared']}\n"
            'Relation: k_app = k∞ + (k∞ b)/Pm. Intercept gives k∞; slope/intercept gives b when units are consistent.\n'
            'At least three points and 10% mean-pressure span are required, but those checks alone do not prove reliable identification. '
            'Stress sensitivity, temperature changes and uncertain porosity can imitate a pressure trend. No propagated uncertainty is claimed.')

    def apply_b(self):
        if self.result is None:return
        b=self.result['fit']['slip_pa']
        control=self.analysis.controls['slip_pa']
        factor=self.analysis.unit_factors()['slip_pa']
        if not control.minimum()<=b/factor<=control.maximum():
            self.status.setText('Estimated b is outside the analysis control range; review the regression.');return
        self.analysis.restored_settings=getattr(self.analysis,'restored_settings',{})|{'slip_pa':b}
        control.setValue(b/factor);self.analysis.inputs_changed()
        self.project.analysis_settings[self.analysis.pulse.uid]=asdict(self.analysis.settings())
        self.analysis.controller.changed()
        self.analysis.status.setText('Applied cross-pulse b as a fixed FDM input. Re-run the FDM fit; it does not become a joint k/b inversion.')
        self.accept()

    def export(self):
        if self.result is None:return
        filename,_=QFileDialog.getSaveFileName(self,'Export Klinkenberg regression','klinkenberg_regression.json','JSON (*.json)')
        if not filename:return
        path=Path(filename).with_suffix('.json')
        from .pulse_capture_io import protect
        try:
            protect(self.project,path)
            session=self.analysis.controller.session_path
            if session and path.resolve()==session.resolve():raise ValueError('Choose a separate result file, not the open pulse session.')
            path.write_text(json.dumps(dict(format='pulse-klinkenberg-result',schema_version=1,analysis=self.result),indent=2,allow_nan=False),encoding='utf-8')
        except Exception as exc:
            QMessageBox.warning(self,'Could not export regression',str(exc));return
        self.status.setText(f'Exported {path}')
