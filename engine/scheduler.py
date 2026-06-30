"""
UnamOS Cron Scheduler
Reads cron triggers from loaded workflows and fires them on schedule.
Runs inside the engine's asyncio event loop.
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


class WorkflowScheduler:
    def __init__(self, engine):
        self._engine = engine
        import datetime as _dt
        tz = _dt.datetime.now(_dt.timezone.utc).astimezone().tzinfo
        self._scheduler = AsyncIOScheduler(timezone=tz)
        self._job_ids: list[str] = []

    def start(self):
        self._scheduler.start()
        self._load_jobs()
        log.info("Cron scheduler started.")

    def reload(self):
        for jid in self._job_ids:
            try:
                self._scheduler.remove_job(jid)
            except Exception:
                pass
        self._job_ids.clear()
        self._load_jobs()
        log.info("Cron jobs reloaded.")

    def _load_jobs(self):
        for schedule, workflow in self._engine.get_cron_workflows():
            wf_name = workflow.get("name", "unknown")
            job_id = f"cron_{wf_name}_{schedule}"
            try:
                parts = schedule.split()
                if len(parts) == 5:
                    minute, hour, day, month, dow = parts
                    import datetime as _dt
                    _tz = _dt.datetime.now(_dt.timezone.utc).astimezone().tzinfo
                    trigger = CronTrigger(
                        minute=minute, hour=hour,
                        day=day, month=month, day_of_week=dow,
                        timezone=_tz,
                    )
                    self._scheduler.add_job(
                        self._fire, trigger,
                        args=[workflow],
                        id=job_id,
                        replace_existing=True,
                    )
                    self._job_ids.append(job_id)
                    log.info("Cron job registered: %s @ %s", wf_name, schedule)
            except Exception as e:
                log.warning("Failed to schedule %s (%s): %s", wf_name, schedule, e)

    async def _fire(self, workflow: dict):
        wf_name = workflow.get("name", "?")
        log.info("Cron trigger firing: %s", wf_name)
        await self._engine.execute(workflow, trigger="cron")

    def stop(self):
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
