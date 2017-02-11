#!/usr/bin/python

'''

Usage:

   $ python zero_csv_plot.py <*.csv file> [-p output_file]

'''

import argparse
import os
import struct
import string
import codecs
import sys
from time import localtime, strftime, gmtime
from collections import OrderedDict
from math import trunc

import md5
import Gnuplot, Gnuplot.funcutils

TIME_FORMAT = '%m/%d/%Y %H:%M:%S'
CSV_TIME_FORMAT = '%d/%m/%Y\t%H:%M:%S'
USE_MBB_TIME = False

def plot_csv(csv_file, png_file):
    g = Gnuplot.Gnuplot(debug=0)
    g('set terminal png crop size 4000,500')
    g('set autoscale')

    g('set grid back xtics ytics')
#   g('set key outside box')
    g('set key top left')

    g('set datafile missing "-1"')
    g('set datafile commentschars "T#!%"')
    g('set datafile separator ";"')
#   g('set decimalsign ","')

    g('set title "Zero Riding/Charging Log Diagramm;  Values without RMP charging "')

    g('set xlabel "Entry"')
#   g('set xdata time')
#   g('set xtics 120 format "%b %d"')
#   g('set format x "%s"')

    g('set ylabel "Werte"')
    g('set y2label "in C/V/A"')

    g('set output "{}"'.format(png_file))
#   g('set yrange [-100:]')

    g('plot "{}" using 1:($3/2) smooth frequency t "Ampere" w lines lw 2, \
        "" using 1:4 smooth frequency t "SOC" w lines lw 2, \
        "" using 1:5 smooth frequency t "Temp C" w lines lw 2, \
        "" using 1:8 smooth frequency t "Volt" w lines lw 2, \
        "" using 1:($9/100) smooth frequency t "RPM * 100" w lines lw 2 ' \
        .format(csv_file))

#    g('set terminal gif crop size 4000,500')
#    g('set output "Test.gif"')
#    g('plot "{}" using 1:($3/2) smooth frequency t "Ampere" w lines lw 2, \
#        "" using 1:4 smooth frequency t "SOC" w lines lw 2, \
#        "" using 1:5 smooth frequency t "Temp C" w lines lw 2, \
#        "" using 1:8 smooth frequency t "Volt" w lines lw 2, \
#        "" using 1:($9/100) smooth frequency t "RPM * 100" w lines lw 2 ' \
#        .format(csv_file))

#        "" using 1:7 smooth frequency t "AmpTemp C" w lines lw 1, \

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('csv_file', help='Zero *.csv log to plot')
    parser.add_argument('-p', '--png', help='PNG Filename')
    args = parser.parse_args()

    log_file = args.csv_file

    out_files = os.path.splitext(args.csv_file)[0]
#    print(md5.new(out_files).hexdigest())

    if args.csv_file:
        csv_file = args.csv_file

    if args.png:
        png_file = args.png
    else:
        png_file = md5.new(out_files).hexdigest()[:17] + out_files[17:] + '.png'

    plot_csv(csv_file, png_file)


