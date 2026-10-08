FROM maxtrade-worker:manager-20261008
RUN /bin/bash -l -c '/opt/venv/bin/python -m pip install "scikit-learn>=1.5,<2" "numpy<3" "pandas>=2.2,<3"'
COPY maxtrade/training.py /app/maxtrade/training.py
COPY scripts/research_worker.py /app/scripts/research_worker.py