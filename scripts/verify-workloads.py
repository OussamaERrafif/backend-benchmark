"""Short integration checks of each k6 workload, separate from benchmark methodology."""
from runner import Controller, SCENARIOS, exclusive, save, utc
import os

os.environ['BENCH_MODE']='quick'
controller=Controller('small')
with exclusive():
    controller.doctor()
    session=controller.session()
    controller.database(reset=True)
    controller.start('fiber')
    try:
        controller.smoke('fiber',session/'fiber/smoke.json')
        for scenario in SCENARIOS:
            controller.database(reset=True)
            controller.load('fiber',scenario,100,'10s',session/'fiber'/scenario/'100-rps'/'warmup-1',measured=False)
            if scenario in ['db-create','db-update','mixed']:controller.database(reset=True)
            row=controller.load('fiber',scenario,100,'20s',session/'fiber'/scenario/'100-rps'/'run-1')
            if row['rejected'] or row.get('error_rate',1)>0:raise RuntimeError('Workload verification failed: '+scenario+' '+str(row))
        # Exercise time-series capture and the stress analysis path using an explicitly
        # short quick diagnostic; it cannot enter research results.
        controller.database(reset=True)
        controller.load('fiber','mixed',100,'20s',session/'fiber/mixed'/'stress',kind='stress')
        save(session/'completed.json',{'timestamp':utc(),'completed':True,'purpose':'All k6 workload integration checks and short time-series diagnostic'})
    finally:
        controller.stop_backends()
        controller.database(reset=True)
print('Workload integration evidence: '+str(session))
