#!/usr/bin/env python3
#
################################################################################
# Name:		make_standalone_plots.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 21, 2018
# Last Changed: July 19, 2026
# Purpose:	Generate performance plots from run_axb.sh / hpc-perftest.sh CSV output
#
# Usage:
#   python3 make_standalone_plots.py [--plot TYPE]
#
# Plot types:
#   unified    (default) compute_time + fileproc_time combined into one line
#   compute    compute_time only
#   fileproc   fileproc_time only
#   separated  compute_time and fileproc_time as separate series per instance
#   cost       estimated cost per run (uses a hardcoded $/min rate)
################################################################################

import argparse
import glob
import itertools
import pathlib
import sys
import time


import matplotlib
matplotlib.use('Agg')  # must precede pyplot import; Agg works on all headless envs including EC2
import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np
import pandas as pd
import seaborn as sns  # noqa: F401 — imported for seaborn plot styling

PLOT_TYPES = ('unified', 'compute', 'fileproc', 'separated', 'cost')

parser = argparse.ArgumentParser(
    description='Generate standalone performance plots from run_axb.sh / hpc-perftest.sh CSV data.',
    epilog=(
        'Expects CSV files in summary_final/ (relative to this script) produced by run_axb.sh / hpc-perftest.sh.\n'
        'Column layout: instance_type, task_array_size, matrix_n, compute_time_sec,\n'
        '               [cols 4-5 unused], fileproc_time_sec, [col 7 alt fileproc].\n'
        'Output PNG files are written to plots/.\n'
        '\n'
        'Plot types:\n'
        '  unified   - compute_time + fileproc_time combined (hours)\n'
        '  compute   - compute_time only (minutes)\n'
        '  fileproc  - fileproc_time only (minutes)\n'
        '  separated - compute and fileproc as separate series per instance (minutes)\n'
        '  cost      - estimated cost at $0.085/min vs matrix size\n'
    ),
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument('--plot', choices=PLOT_TYPES, default='unified',
    help='which data series to plot (default: unified)')
args = parser.parse_args()

timestamp = time.strftime("%d-%b-%Y-%H:%M:%S", time.localtime())
plotfile = f'plot_standalone_{args.plot}.{timestamp}.png'

_script_dir  = pathlib.Path(__file__).parent.parent.resolve()
_summary_dir = _script_dir / 'summary_final'
_plots_dir   = _script_dir / 'plots'

if not _summary_dir.is_dir():
    sys.exit(f"ERROR: summary_final/ not found at {_summary_dir}\n"
             f"Run run_axb.sh / hpc-perftest.sh first to generate CSV data.")

_plots_dir.mkdir(parents=True, exist_ok=True)

MATRIX_SIZES = ''
_conf = _script_dir / 'MATRIX_SIZES.conf'
if not _conf.is_file():
    sys.exit(f"ERROR: MATRIX_SIZES.conf not found at {_conf}")
with open(_conf) as _mf:
    for line in _mf:
        if line.startswith('MATRIX_SIZES='):
            MATRIX_SIZES = line.split('=', 1)[1].strip().strip('"')

print('')
print('Generating graphs of the CSV data files generated from run_axb.sh / hpc-perftest.sh...')

plt.figure(figsize=(11.5, 8))
valid_markers = mpl.markers.MarkerStyle.filled_markers
_marker_cycle = itertools.cycle(valid_markers)
x_axis_max = []
y_axis_max = []

for datafile in glob.iglob(str(_summary_dir / '*.csv')):
    df = pd.read_csv(datafile, delimiter=',')
    label = pathlib.Path(datafile).stem.replace('summary.', '').split('.', 1)[0]
    m1 = next(_marker_cycle)
    m2 = next(_marker_cycle)

    if args.plot == 'unified':
        df['task_array_size'] = df[df.columns[2:3]]
        df['compute_time']    = df[df.columns[3:4]] / 3600
        df['fileproc_time']   = df[df.columns[6:7]] / 3600
        df['total_compute_time'] = df['compute_time'] + df['fileproc_time']
        x_axis_max.append(max(df['total_compute_time']))
        y_axis_max.append(max(df['task_array_size']))
        plt.plot('total_compute_time', 'task_array_size', data=df,
                 label=label, marker=m1, markersize=5, linestyle='None')

    elif args.plot == 'compute':
        df['task_array_size'] = df[df.columns[2:3]]
        df['compute_time']    = df[df.columns[3:4]] / 60
        x_axis_max.append(max(df['compute_time']))
        y_axis_max.append(max(df['task_array_size']))
        plt.plot('compute_time', 'task_array_size', data=df,
                 label=label, marker=m1, markersize=5, linestyle='None')

    elif args.plot == 'fileproc':
        df['task_array_size'] = df[df.columns[2:3]]
        df['fileproc_time']   = df[df.columns[6:7]] / 60
        x_axis_max.append(max(df['fileproc_time']))
        y_axis_max.append(max(df['task_array_size']))
        plt.plot('fileproc_time', 'task_array_size', data=df,
                 label=label, marker=m1, markersize=5, linestyle='None')

    elif args.plot == 'separated':
        df['task_array_size'] = df[df.columns[2:3]]
        df['compute_time']    = df[df.columns[3:4]] / 60
        df['fileproc_time']   = df[df.columns[6:7]] / 60
        df['total_compute_time'] = df['compute_time'] + df['fileproc_time']
        x_axis_max.append(max(df['total_compute_time']))
        y_axis_max.append(max(df['task_array_size']))
        plt.plot('compute_time', 'task_array_size', data=df,
                 label=label + '.compute', marker=m1, markersize=5, linestyle='None')
        plt.plot('fileproc_time', 'task_array_size', data=df,
                 label=label + '.fileproc', marker=m2, markersize=5, linestyle='None')

    elif args.plot == 'cost':
        df['task_array_size'] = df[df.columns[2:3]]
        df['compute_time']    = df[df.columns[3:4]] / 60
        df['fileproc_time']   = df[df.columns[6:7]] / 60
        df['total_compute_time'] = df['compute_time'] + df['fileproc_time']
        instance_min_cost = 0.085  # c5.xlarge on-demand $/min as reference rate
        df['cost'] = df['total_compute_time'] * instance_min_cost
        y_axis_max.append(max(df['task_array_size']))
        plt.plot('cost', 'task_array_size', data=df,
                 label=label, marker=m1, markersize=5, linestyle='None')

if not y_axis_max:
    sys.exit(f"ERROR: no CSV files found in {_summary_dir}\nRun run_axb.sh / hpc-perftest.sh first.")

titles = {
    'unified':   'Standalone Instance Random Matrix Computation and Data File Processing Performance\n'
                 'Generated Using run_axb.sh / hpc-perftest.sh and Axb_random.py\n',
    'compute':   'Standalone Instance Random Matrix Computation Performance\n'
                 'Generated Using run_axb.sh / hpc-perftest.sh and Axb_random.py\n',
    'fileproc':  'Standalone Instance Data File Processing Performance\n'
                 'Generated Using run_axb.sh / hpc-perftest.sh and Axb_random.py Logs\n',
    'separated': 'Standalone Instance Random Matrix Computation and Data File Processing Performance\n'
                 'Generated Using run_axb.sh / hpc-perftest.sh and Axb_random.py and Displayed as Separate Data Sets\n',
    'cost':      'Standalone Instance Random Matrix Computation and Data File Processing Performance\n'
                 'Generated Using run_axb.sh / hpc-perftest.sh and Axb_random.py\n',
}
xlabels = {
    'unified':   'Total Compute Time (Hours)',
    'compute':   'Total Compute Time (Minutes)',
    'fileproc':  'Total File Processing Time (Minutes)',
    'separated': 'Total Compute Time (Minutes)',
    'cost':      'Cost ($/min)',
}

plt.title(titles[args.plot] + MATRIX_SIZES, weight='bold')
plt.legend(loc='best', fontsize='small', markerscale=0.8, title='DataSource_InstanceType')
plt.xlabel(xlabels[args.plot], fontweight='bold')
plt.ylabel('Matrix Dimensions (N x N)', fontweight='bold')
plt.xlim(xmin=0)
plt.ylim(ymin=0)
plt.tight_layout()

if args.plot != 'cost' and x_axis_max:
    plt.xticks(np.arange(0, max(x_axis_max) + 0.5, 0.5))
if y_axis_max:
    step = 1000 if args.plot == 'unified' else 500
    plt.yticks(np.arange(0, max(y_axis_max) + step, step))
plt.minorticks_on()

_plotpath = _plots_dir / plotfile
plt.savefig(_plotpath, format='png')
print('')
print(f'Creating and saving  ==>  {_plotpath}')
print('')
print('Copy this file onto your local machine to view.')
print('Exiting...')
print('')
sys.exit(0)
