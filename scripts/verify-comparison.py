"""All-eight, one-scenario measurement gate before a full research experiment."""
import os
from runner import Controller, FRAMEWORKS, exclusive, save, utc, command, ROOT

os.environ['BENCH_MODE']='quick'
os.environ['SCENARIOS']='json'
controller=Controller('small')
# This is explicitly a single-load verification, not the standard quick load sweep.
controller.config['RPS_STEPS']='100'
with exclusive():
    controller.doctor()
    session=controller.session()
    rows=[]
    try:
        controller.smoke_all(session)
        for framework in FRAMEWORKS:
            controller.database(reset=True);controller.start(framework)
            try:
                directory=session/framework/'json'/'100-rps'
                controller.load(framework,'json',100,'10s',directory/'warmup-1',measured=False)
                rows.append(controller.load(framework,'json',100,'20s',directory/'run-1'))
            finally:controller.compose('stop',framework,check=False)
        valid=all(not row['rejected'] for row in rows)
        save(session/'completed.json',{'timestamp':utc(),'completed':valid,'purpose':'All-eight JSON measurement gate at one common 100-RPS load; not a load sweep','valid_frameworks':sum(not row['rejected'] for row in rows),'passing_frameworks':sum(row['sustainable'] for row in rows)})
        # Threshold failures are valid results. Only corrupt/missing measurements
        # fail the collection gate; passing_frameworks reports sustainability separately.
        if not valid:raise RuntimeError('Comparison measurement collection failed; inspect preserved states')
    except Exception as error:
        save(session/'failure.json',{'timestamp':utc(),'error':str(error)})
        raise
    finally:
        controller.stop_backends();controller.database(reset=True)
        command(['python',str(ROOT/'analysis/pipeline.py')])
print('Comparison verification evidence: '+str(session))
