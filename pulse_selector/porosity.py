"""Closed-system gas-balance pore volume from captured before/after states.

No Qt dependency. Ideal-gas Boyle balance and optional temperature/PR-N2
correction. Core initially equilibrated with downstream is an explicit input
assumption, not something inferred from the pressure trace.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import numpy as np
from .permeability import extract_trace, initial_upstream_state,validate_absolute_states
from .calibration import nitrogen_z
from .pressure_display import pressure_text


@dataclass(frozen=True)
class PorosityOptions:
    temperature_mode: str = 'Common temperature'
    common_temperature_c: float = 25.0
    upstream_temperature_key: str = ''
    downstream_temperature_key: str = ''
    core_temperature_key: str = ''  # '__downstream__' explicitly selects a proxy.
    eos: str = 'Ideal gas (Z=1)'
    final_pressure_tolerance_pa: float = 1000.0
    final_drift_limit_pa_min: float = 1000.0

    def validate(self):
        if self.temperature_mode not in ('Common temperature','Captured temperatures'):
            raise ValueError('Choose common or captured temperatures.')
        if self.eos not in ('Ideal gas (Z=1)','Peng–Robinson nitrogen'):
            raise ValueError('Choose ideal gas or Peng–Robinson nitrogen.')
        if not np.isfinite(self.common_temperature_c) or self.common_temperature_c <= -273.15:
            raise ValueError('Common temperature must be above absolute zero.')
        for value in (self.final_pressure_tolerance_pa,self.final_drift_limit_pa_min):
            if not np.isfinite(value) or value <= 0:
                raise ValueError('Final-state pressure mismatch and drift limits must be positive.')


def gas_balance(*, upstream_volume_m3, downstream_volume_m3, length_m, diameter_m,
                initial_upstream_pa, initial_downstream_pa, final_pressure_pa,
                temperatures_k, eos='Ideal gas (Z=1)', upstream_dead_volume_m3=0.0,
                initial_dead_pressure_pa=None):
    """Solve the gas-inventory equation for accessible core pore volume.

    temperatures_k has ui, di, ci, uf, df, cf. Initial core pressure is Pdi;
    final pressure is common. Final temperatures may differ by compartment.
    upstream_volume_m3 is VU+DU. Optional DU starts at its own pressure and
    uses the selected core/proxy temperature initially and finally. DU=0
    preserves the historical fully precharged upstream-storage balance.
    All compartment volumes are rigid. No leak, adsorption or gas reaction.
    """
    scalars=[upstream_volume_m3,downstream_volume_m3,length_m,diameter_m,
             initial_upstream_pa,initial_downstream_pa,final_pressure_pa]
    if not np.isfinite(scalars).all() or min(scalars)<=0:
        raise ValueError('Volumes, dimensions and absolute pressures must be finite and positive.')
    roles=('ui','di','ci','uf','df','cf')
    if set(temperatures_k)!=set(roles) or any(not np.isfinite(temperatures_k[k]) or temperatures_k[k]<=0 for k in roles):
        raise ValueError('Supply six finite positive temperatures in kelvin.')
    if eos not in ('Ideal gas (Z=1)','Peng–Robinson nitrogen'):
        raise ValueError('Unsupported gas-balance EOS.')
    du=upstream_dead_volume_m3
    pdu=initial_downstream_pa if initial_dead_pressure_pa is None else initial_dead_pressure_pa
    if not np.isfinite([du,pdu]).all() or not 0<=du<upstream_volume_m3 or pdu<=0:
        raise ValueError('Require 0 ≤ DU < VU+DU and positive absolute initial DU pressure.')
    charged=upstream_volume_m3-du
    temperatures_k=dict(temperatures_k)
    pressures=dict(ui=initial_upstream_pa,di=initial_downstream_pa,ci=initial_downstream_pa,
                   uf=final_pressure_pa,df=final_pressure_pa,cf=final_pressure_pa)
    if du>0:
        roles+=('dui','duf')
        temperatures_k.update(dui=temperatures_k['ci'],duf=temperatures_k['cf'])
        pressures.update(dui=pdu,duf=final_pressure_pa)
    z={k:(1.0 if eos=='Ideal gas (Z=1)' else nitrogen_z(pressures[k]/1000,temperatures_k[k])) for k in roles}
    density={k:pressures[k]/(z[k]*temperatures_k[k]) for k in roles}  # nR/V, Pa/K
    numerator=(charged*(density['ui']-density['uf'])+
               downstream_volume_m3*(density['di']-density['df']))
    if du>0:
        numerator+=du*(density['dui']-density['duf'])
    denominator=density['cf']-density['ci']
    scale=max(abs(density['cf']),abs(density['ci']))
    if denominator <= 1e-8*scale:
        raise ValueError('Core gas-density increase is zero, negative or too small; this downstream-equilibrated expansion cannot determine pore volume reliably.')
    vp=numerator/denominator
    bulk=np.pi*diameter_m**2*length_m/4
    phi=vp/bulk
    if not np.isfinite(phi) or not 0 < phi < 1:
        raise ValueError(f'Gas balance gives nonphysical porosity {phi:.5g} (pore volume {vp*1e6:.5g} cm³). Check initial core pressure, volume boundaries, equilibrium, temperatures, pressure offsets and gas model.')
    initial_inventory=(charged*density['ui']+downstream_volume_m3*density['di']+vp*density['ci'])
    final_inventory=(charged*density['uf']+downstream_volume_m3*density['df']+vp*density['cf'])
    if du>0:
        initial_inventory+=du*density['dui']
        final_inventory+=du*density['duf']
    return dict(porosity_fraction=float(phi),porosity_percent=float(phi*100),
        pore_volume_cm3=float(vp*1e6),bulk_volume_cm3=float(bulk*1e6),
        charged_upstream_volume_cm3=float(charged*1e6),upstream_dead_volume_cm3=float(du*1e6),
        eos=eos,initial_core_pressure_pa=float(initial_downstream_pa),
        final_common_pressure_pa=float(final_pressure_pa),pressures_pa=pressures,
        temperatures_k={k:float(v) for k,v in temperatures_k.items()},z_factors=z,
        concentration_proxy_pa_per_k=density,
        core_density_relative_change=float(denominator/scale),
        inventory_closure_pa_m3_per_k=float(initial_inventory-final_inventory))


def porosity_from_pulse(project,pulse,settings,options):
    """Use the captured states, not an assumed final equilibrium from the FDM."""
    options.validate()
    # Porosity estimation is independent of the entered porosity, k bounds and
    # transient fitting windows. Pressure basis/channel validation still applies.
    trace=extract_trace(project,pulse,settings,preview=True)
    validate_absolute_states(trace)
    storage=initial_upstream_state(trace,settings)
    pressures=[trace.final_upstream_pa,trace.final_downstream_pa]
    mismatch=abs(pressures[0]-pressures[1])
    if mismatch>options.final_pressure_tolerance_pa:
        raise ValueError(f'Final pressure windows differ by {mismatch/1000:.4g} kPa ({pressure_text(mismatch,"psi")}); limit is {options.final_pressure_tolerance_pa/1000:g} kPa ({pressure_text(options.final_pressure_tolerance_pa,"psi")}). Choose equilibrated final states or inspect sensor offsets.')
    final_pressure=float(np.mean(pressures))
    for key in (settings.upstream_key,settings.downstream_key):
        slope=abs(pulse.final[key].slope_per_min*1000)
        if slope>options.final_drift_limit_pa_min:
            raise ValueError(f'{pulse.final[key].source_column}: final pressure drifts {slope/1000:.4g} kPa/min ({pressure_text(slope,"psi")}/min), above the entered limit. Choose a stabilized final interval.')
    warnings=['Closed rigid system; no leaks, adsorption, gas reaction or trapped volumes outside the calibrated boundaries.',
              'Core initially at downstream pressure; final core pressure equals the mean of the two final pressure windows.',
              'This is connected gas-accessible porosity under the test conditions, not necessarily total porosity.']
    if settings.upstream_pressure_correction_pa or settings.downstream_pressure_correction_pa:
        warnings.append(f'User-entered sensor corrections applied to all states: upstream {settings.upstream_pressure_correction_pa:.7g} Pa; downstream {settings.downstream_pressure_correction_pa:.7g} Pa. Verify their calibration independently.')
    for key in (settings.upstream_key,settings.downstream_key):
        if abs(pulse.initial[key].slope_per_min*1000)>options.final_drift_limit_pa_min:
            warnings.append(f'{pulse.initial[key].source_column}: initial pressure window is drifting; its mean may not represent the pressure just before valve opening. Review that window.')
    temperatures={k:options.common_temperature_c+273.15 for k in ('ui','di','ci','uf','df','cf')}
    snapshot=project.snapshots[pulse.dataset_id]
    if options.temperature_mode=='Captured temperatures':
        keys=dict(u=options.upstream_temperature_key,d=options.downstream_temperature_key,
                  c=options.downstream_temperature_key if options.core_temperature_key=='__downstream__' else options.core_temperature_key)
        channels={c.key:c for c in snapshot.channels}
        for compartment,key in keys.items():
            if key not in channels or channels[key].quantity!='Temperature':
                raise ValueError('Assign upstream, downstream and core/proxy temperature channels.')
            for state,suffix in ((pulse.initial,'i'),(pulse.final,'f')):
                temperatures[compartment+suffix]=float(state[key].mean+273.15)
        if options.core_temperature_key=='__downstream__':
            warnings.append('Core temperature is approximated by the selected downstream sensor; confirm this proxy is appropriate.')
        for key in set(keys.values()):
            if any(abs(state[key].slope_per_min)>.1 for state in (pulse.initial,pulse.final)):
                warnings.append(f'{key}: temperature drift exceeds 0.1 °C/min in a state window; review whether state-average temperatures are representative.')
        if pulse.initial_temperature_bounds!=pulse.initial_pressure_bounds or pulse.final_temperature_bounds!=pulse.final_pressure_bounds:
            warnings.append('Pressure and temperature state windows differ; confirm they represent the same initial/final states.')
    else:
        warnings.append('Common-temperature assumption: all compartments/states use the entered temperature.')
        for c in snapshot.channels:
            if c.quantity=='Temperature' and abs(pulse.final[c.key].mean-pulse.initial[c.key].mean)>.5:
                warnings.append(f'{c.source_column} changes by more than 0.5 °C; compare with captured-temperature correction.')
    result=gas_balance(upstream_volume_m3=settings.upstream_volume_m3,
        downstream_volume_m3=settings.downstream_volume_m3,length_m=settings.length_m,
        diameter_m=settings.diameter_m,initial_upstream_pa=trace.initial_upstream_pa,
        initial_downstream_pa=trace.initial_downstream_pa,final_pressure_pa=final_pressure,
        temperatures_k=temperatures,eos=options.eos,
        upstream_dead_volume_m3=storage['dead_volume_m3'],
        initial_dead_pressure_pa=storage['initial_dead_pressure_pa'])
    if storage['dead_volume_m3']>0:
        warnings.append('VU is charged before valve opening; DU uses the configured initial pressure and selected core/proxy temperature. Downstream storage VD+DD and core start at measured downstream pressure.')
    else:
        warnings.append('Combined storage: all VU+DU is assumed initially at the upstream plateau. Select Isolated tank if only VU was charged.')
    if result['core_density_relative_change']<.05:
        warnings.append('Small core gas-density change (<5%): pressure/temperature errors can strongly affect pore volume.')
    if options.eos=='Ideal gas (Z=1)':
        warnings.append('Z=1 assumed. Use the nitrogen EOS option only if nitrogen is the actual test gas and temperatures are known.')
    result.update(method='Closed-system gas balance',initial_storage=storage,temperature_mode=options.temperature_mode,
        final_pressure_mismatch_pa=float(mismatch),settings=dict(length_m=settings.length_m,
        diameter_m=settings.diameter_m,upstream_volume_m3=settings.upstream_volume_m3,
        downstream_volume_m3=settings.downstream_volume_m3,upstream_key=settings.upstream_key,
        downstream_key=settings.downstream_key,initial_upstream_mode=settings.initial_upstream_mode,
        upstream_dead_volume_m3=settings.upstream_dead_volume_m3,dead_pressure_mode=settings.dead_pressure_mode,
        initial_dead_pressure_pa=settings.initial_dead_pressure_pa),options=asdict(options),warnings=warnings)
    result['settings'].update(upstream_pressure_correction_pa=settings.upstream_pressure_correction_pa,
                              downstream_pressure_correction_pa=settings.downstream_pressure_correction_pa)
    return result


def porosity_fingerprint(project,pulse,settings,options):
    """Exclude entered phi and fit settings: they are not gas-balance inputs."""
    snapshot=project.snapshots[pulse.dataset_id]
    samples=snapshot.data.iloc[pulse.context_start_row:pulse.decay_end_row+1][['Time']+[c.key for c in snapshot.channels]]
    samples=samples.astype(float)
    signature=dict(algorithm='closed-gas-balance-2',pulse=asdict(pulse),options=asdict(options),
        geometry=[settings.length_m,settings.diameter_m],
        volumes=[settings.upstream_volume_m3,settings.downstream_volume_m3],
        initial_storage=[settings.initial_upstream_mode,settings.upstream_dead_volume_m3,
                         settings.dead_pressure_mode,settings.initial_dead_pressure_pa],
        pressure_corrections=[settings.upstream_pressure_correction_pa,settings.downstream_pressure_correction_pa],
        pressure_keys=[settings.upstream_key,settings.downstream_key],
        basis=project.pressure_basis,barometric_kpa=project.barometric_kpa,
        channels=[asdict(c) for c in snapshot.channels],
        sample_hash=sha256(samples.to_json(orient='split',double_precision=10).encode()).hexdigest())
    return sha256(json.dumps(signature,sort_keys=True,allow_nan=False).encode()).hexdigest()
