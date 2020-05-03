#!/bin/sh

if [ -z "$1" ]; then
  python3 zero_log_parser.py -h
else
  python3 zero_log_parser.py $@
fi
