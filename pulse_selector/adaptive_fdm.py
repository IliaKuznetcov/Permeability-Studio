"""Adaptive integration of the reference FDM's continuous-time spatial equations.

Different time integrator; same grid, slip law, reservoir boundary coefficients,
initial node values and half-cell storage. The original fdm_solver is unchanged.
Integrate scaled (P+b) directly: the chain rule cancels the (P+b) factor in
the transformed-pressure Euler update. This also exposes its linear inventory.
"""
import numpy as np
from scipy.integrate import solve_ivp
from scipy.sparse import diags

ENGINE_VERSION='adaptive-bdf-1'
RTOL=1e-8
ATOL_PA=.01


def run_adaptive_fdm(*,V_up,V_down,L,D,porosity,k,mu,b,P_up_initial,
                     P_down_initial,N=40,t_end=5000.,max_evaluations=250000,
                     check_cancel=None,rtol=RTOL,atol_pa=ATOL_PA):
    """Return reservoir histories and a dense evaluator at arbitrary sample times.

    max_evaluations bounds RHS calls, rather than explicit Euler steps. Accuracy
    is controlled by rtol/atol, not the reference solver's safety factor.
    """
    scalars=[V_up,V_down,L,D,porosity,mu,P_up_initial,P_down_initial,t_end,rtol,atol_pa]
    if not np.isfinite(scalars).all() or min(scalars)<=0 or not 0<porosity<1:
        raise ValueError('Adaptive FDM requires finite positive physical inputs and tolerances.')
    if not np.isfinite([k,b]).all() or min(k,b)<0 or int(N)!=N or N<4 or max_evaluations<1:
        raise ValueError('Invalid adaptive FDM permeability, slip, grid or evaluation budget.')
    if check_cancel:check_cancel()
    area=np.pi*D**2/4
    dx=L/N
    pore_cell=porosity*area*dx
    weights=np.full(N+1,pore_cell)
    weights[0]=V_up+pore_cell/2
    weights[-1]=V_down+pore_cell/2
    scale=max(P_up_initial,P_down_initial)+b
    initial=np.full(N+1,(P_down_initial+b)/scale)
    initial[0]=(P_up_initial+b)/scale
    c=k*scale/(2*porosity*mu*dx**2)
    cu=k*area*scale/(2*mu*dx*weights[0])
    cd=k*area*scale/(2*mu*dx*weights[-1])
    calls=0

    def rhs(t,u):
        nonlocal calls
        if check_cancel:check_cancel()
        calls+=1
        if calls>max_evaluations:
            raise ValueError(f'Adaptive FDM exceeded {max_evaluations:,} RHS evaluations. Increase Maximum solver work or inspect the physical inputs.')
        squared=u*u
        result=np.empty_like(u)
        result[1:-1]=c*(squared[2:]-2*squared[1:-1]+squared[:-2])
        result[0]=cu*(squared[1]-squared[0])
        result[-1]=cd*(squared[-2]-squared[-1])
        return result

    def jacobian(t,u):
        if check_cancel:check_cancel()
        diagonal=-4*c*u
        diagonal[0]=-2*cu*u[0]
        diagonal[-1]=-2*cd*u[-1]
        lower=2*c*u[:-1]
        lower[-1]=2*cd*u[-2]
        upper=2*c*u[1:]
        upper[0]=2*cu*u[1]
        return diags((lower,diagonal,upper),(-1,0,1),format='csc')

    solution=solve_ivp(rhs,(0.,float(t_end)),initial,method='BDF',jac=jacobian,
                       rtol=rtol,atol=atol_pa/scale,dense_output=True)
    if check_cancel:check_cancel()
    if not solution.success:
        raise ValueError('Adaptive FDM failed: '+solution.message)
    pressure=solution.y*scale-b
    if not np.isfinite(pressure).all() or np.any(pressure<=0):
        raise ValueError('Adaptive FDM produced invalid absolute pressure; no result accepted.')
    inventory=weights@pressure
    error=(inventory-inventory[0])/inventory[0]

    def evaluate(times):
        if check_cancel:check_cancel()
        times=np.asarray(times,dtype=float)
        tolerance=32*np.finfo(float).eps*max(1.,t_end)
        if not np.isfinite(times).all() or np.any(times < -tolerance) or np.any(times > t_end+tolerance):
            raise ValueError('Adaptive FDM interpolation requested outside its simulated interval.')
        values=solution.sol(np.clip(times,0,t_end))[[0,-1]]*scale-b
        if not np.isfinite(values).all() or np.any(values<=0):
            raise ValueError('Adaptive FDM interpolation produced invalid absolute pressure.')
        return values

    return dict(x=np.linspace(0,L,N+1),time=solution.t,P_up=pressure[0],
        P_down=pressure[-1],delta_P=pressure[0]-pressure[-1],P_final=pressure[:,-1],
        dx=dx,dt=None,num_steps=len(solution.t)-1,inventory=inventory,
        inventory_error=error,max_inventory_error=float(np.max(abs(error))),
        evaluate=evaluate,nfev=solution.nfev,njev=solution.njev,nlu=solution.nlu,
        engine_version=ENGINE_VERSION,rtol=rtol,atol_pa=atol_pa)
