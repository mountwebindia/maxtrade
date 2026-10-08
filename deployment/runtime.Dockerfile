FROM maxtrade-worker:34388ef
COPY maxtrade/ /app/maxtrade/
COPY scripts/monitor_worker.py scripts/research_worker.py /app/scripts/