FROM python:3.8-alpine

COPY . /zero_log_parser
WORKDIR /zero_log_parser

RUN python3 setup.py develop \
&&  mv /zero_log_parser/docker-entrypoint.sh /usr/bin

ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
