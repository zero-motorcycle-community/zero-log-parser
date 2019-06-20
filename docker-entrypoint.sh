#!/bin/sh

if [ -z $1 ]; then
  python zero_log_parser.py -h
else
  python zero_log_parser.py $@
fi
