#!/usr/bin/env python3
#
################################################################################
# Name:		make_standalone_separated_plots.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 21, 2018
# Last Changed: April 22, 2018
# Purpose:	Make plots with bang.sh data generated on standalone instances
#		separating compute_time and fileproc_time 
################################################################################
#
# Generate a timestamp to give the plot a unique filename.

import time
timestamp = time.strftime("%d-%b-%Y-%H:%M:%S", time.localtime())
plotfile = 'plot_standalone_separated.' + timestamp + '.png'

# Import some required Python libraries.

import argparse
import glob
import matplotlib as mpl
import numpy as np
import os
import pandas as pd
import pathlib
import requests
import sys

# Determine if we are running on an EC2 instance to ensure the Python libraries
# required for plotting the graphs are loaded with the correct backend.

try:
    r = requests.get('http://169.254.169.254/latest/meta-data/instance-id', timeout = 2)
    ec2_instance_status = 'True'
    mpl.use('Agg')
except requests.exceptions.RequestException as e:
    ec2_instance_status = 'False'

# Import the Python libraries required to plot the graphs.

import matplotlib.pyplot as plt
import seaborn as sns

# Create a directory to hold the plots.

print("")
print("Generating graphs of the CSV data files generated from bang.sh...")
pathlib.Path('plots').mkdir(parents=True, exist_ok=True)

# Parse the sizes of the tested matrices for the plot title.

for line in open("MATRIX_SIZES.conf"):
   if "MATRIX_SIZES=\"" in line:
       MATRIX_SIZES = line

# Create Pandas data frames from the CSV input file.
# Generate x-y regression plots from the data frames using random markers.
# Time is measured in minutes.  Adjust as needed when running longer tests.

os.chdir('summary_final/')
plt.figure(figsize=(11.5,8))
valid_markers = mpl.markers.MarkerStyle.filled_markers
x_axis_max = []
y_axis_max = []

for datafile in glob.iglob("*.csv"):
    df = pd.read_csv(datafile, delimiter=',')
    df['task_array_size'] = df[df.columns[1:2]]
    df['compute_time'] = df[df.columns[2:3]]/60
    df['fileproc_time'] = df[df.columns[6:7]]/60
    df['total_compute_time'] = df['compute_time'] + df['fileproc_time']
    datafile=datafile.replace('summary.','').split(".",1 )[0]
    x_axis_max.append(max(df['total_compute_time']))
    y_axis_max.append(max(df['task_array_size']))
    markers = np.random.choice(valid_markers, df.shape[1], replace=False)
    a = plt.plot('compute_time', 'task_array_size', data=df, label=datafile + '.compute', marker=markers[1], markersize=5, linestyle='None')
    b = plt.plot('fileproc_time', 'task_array_size', data=df, label=datafile + '.fileproc', marker=markers[1], markersize=5, linestyle='None')

# Format the plot.

os.chdir('../plots/')
plt.title('Standalone Instance Random Matrix Computation and Data File Processing Performance\nGenerated Using bang.sh and Axb_random.py and Displayed as Separate Data Sets\n%s' % (MATRIX_SIZES), weight='bold')
plt.legend(loc='best', fontsize='small', markerscale=0.8, title='DataSource_InstanceType')
plt.xlabel('Total Compute Time (Minutes)', fontweight='bold')
plt.ylabel('Matrix Dimensions (N x N)', fontweight='bold')
plt.xlim(xmin=0)
plt.ylim(ymin=0)
plt.tight_layout()

# Set the axes ticks based on the max values of N and total_compute_time.
# Adjust as needed when working with longer running tests.

plt.xticks(np.arange(0, max(x_axis_max)+0.5, 0.5))
plt.yticks(np.arange(0, max(y_axis_max)+500, 500))
plt.minorticks_on()

# Save the plot as a PNG file to the plots/ subdirectory.
# Display the plot in a popup window if this isn't an EC2 instance (which will
# be headless by default).

plt.savefig(plotfile, format='png')
print("")
if ec2_instance_status == 'False':
    print("Now displaying  ===>   plots/%s" % plotfile)
    print("")
    print("Please close the Python window to regain control of ths shell.")
    plt.show()
else:
    print("Creating and saving  ==>  plots/%s" % plotfile)
    print("")
    print("Copy this file onto your local machine to view.")
    print("Exiting...")
    print("")
    sys.exit("")
