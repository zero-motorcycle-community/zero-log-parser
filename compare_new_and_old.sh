#!/usr/bin/env bash

for log_file in *.bin
do
    old_output="${log_file%.bin}.txt"
    new_output="${log_file%.bin}.new.txt"
    diff -w "$old_output" "$new_output"
done
