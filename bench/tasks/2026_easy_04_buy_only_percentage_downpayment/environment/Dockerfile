FROM erpbench-dynamic-base:20260908
USER root
COPY setup_scenario.py /setup/setup_scenario.py
COPY scenario_data.json /setup/scenario_data.json
COPY entrypoint.sh /usr/local/bin/harbor-entrypoint.sh
RUN chmod +x /usr/local/bin/harbor-entrypoint.sh
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/harbor-entrypoint.sh"]
