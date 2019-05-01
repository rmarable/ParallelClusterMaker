#!/usr/bin/env python3
#
################################################################################
# Name:		make_sge_cluster_plots.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 23, 2018
# Last Changed: April 23, 2018
# Purpose:	Make PNG plots using performance data gathered from bang.sh
#		jobs running on Grid Engine-based cluster stacks
################################################################################
#
# Generate a timestamp to give the plot a unique filename.

import time
timestamp = time.strftime("%d-%b-%Y-%H:%M:%S", time.localtime())
plotfile = 'plot_sge_job_data.' + timestamp + '.png'

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

# Determine if we are running on an EC2 instance to ensure the Python
# libraries required for plotting the graphs are loaded with the correct
# backend.

try:
    r = requests.get('http://169.254.169.254/latest/meta-data/instance-id', timeout = 2)
    ec2_instance_status = 'True'
    mpl.use('Agg')
except requests.exceptions.RequestException as e:
    ec2_instance_status = 'False'

# Import the Python libraries required to plot the graphs.

import matplotlib.pyplot as plt
import seaborn as sns

# Create a directory to store the plots.

print("")
print("Generating graphs of the CSV data files generated from bang.sh...")
pathlib.Path('plots').mkdir(parents=True, exist_ok=True)

# Parse the sizes of the tested matrices for the plot title.

for line in open("MATRIX_SIZES.conf"):
    if "MATRIX_SIZES=\"" in line:
        MATRIX_SIZES = line
             
# Create Pandas data frames from the CSV summary file.
# Generate x-y regression plots from the data frames.
# Set the x- and y-axis maximum values based on the data to be plotted.
# Use dynamic markers.

os.chdir('sge_job_data/')
plt.figure(figsize=(11.5,8))
valid_markers = mpl.markers.MarkerStyle.filled_markers
x_axis_max = []
y_axis_max = []

for datafile in glob.iglob("*.csv"):
    df = pd.read_csv(datafile, delimiter=',')
    df['task_array_size'] = df[df.columns[3:4]]
    df['sge_queue_wait_time'] = df[df.columns[4:5]]/3600
    df['sge_job_execute_time'] = df[df.columns[5:6]]/3600
    df['total_compute_time'] = df[df.columns[6:7]]/3600
    x_axis_max.append(max(df['total_compute_time']))
    y_axis_max.append(max(df['task_array_size']))
    datafile=datafile.replace('.job_Axb_random.',', jobID = ').replace('.csv', '').split(".",1 )[0]
    markers = np.random.choice(valid_markers, df.shape[1], replace=False)
    sns.regplot('total_compute_time', 'task_array_size', data=df, order=2, fit_reg=True, truncate=True, ci=None, label=datafile, marker=markers[1], scatter_kws={'s':15})

# Format the plot title, legend, and axes.

os.chdir('../plots/')
plt.title('HPC Cluster Performance Data Generated With bang.sh and Axb_random.py \n%s' % (MATRIX_SIZES), weight='bold')
407
#plt.legend(loc='best', fontsize='small', markerscale=0.75, title=datafile)
plt.legend(loc='best', fontsize='small', markerscale=0.75, title='ClusterName_InstanceType')
plt.xlabel('Total Run Time (Hours)', fontweight='bold')
plt.ylabel('Task Array Size (Jobs)', fontweight='bold')
plt.xlim(xmin=0)
plt.ylim(ymin=0)
plt.tight_layout()

# Set the axes ticks based on the maximum value of task_array_size and
# total_compute_time.  Adjust as needed when running longer tests.

plt.xticks(np.arange(0, max(x_axis_max)+5, 5))
plt.yticks(np.arange(0, max(y_axis_max)+5, 5))
plt.minorticks_on()

# Save the plot as a PNG file to the plots/ subdirectory.
# Display the plot in a popup window if this isn't an EC2 instance (which is
# headless by default).

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
