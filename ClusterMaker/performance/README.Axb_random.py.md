# README.Axb_random.py.md

## Background

Axb_random.py is a Python3 tool that was created to evaluate the performance
of HPC clusters and EC2 instances using a common framework.  It can be used 
o evaluate onprem virtual machine performance and for diving deeper into 
performance differences between EC2 instance types, ParallelCluster stacks,
ECS containers, etc.

Axb_random.py solves Ax=b when A and b are N x N square matrices populated
with randomly generated floats calculated from the normal distribution using
randomly generated standard deviations (sigma) between 0 and sqrt(10) and an
origin (mu) equal to zero.

http://www.mathsisfun.com/algebra/systems-linear-equations-matrices.html
https://www.mathsisfun.com/data/standard-normal-distribution.html

Axb_random.py also provides options for the operator to generate a dump of
the script output to the console, a text file containing A, b, x, and all
sigma values, and/or CSV files for offline stastical analysis of the time
required to compute the solution matrix x.  All matrix elements and sigma
values are rounded to 4 decimal places.

For user convenience, a wrapper script called "bang.sh" is also provided
that invokes Axb_random.py with multiple parameters and summarizes the data
into CSV files suitable for offline analysis. Both standalone operation and
cluster submission are supported.  Please review the inline comments for
guidance.

A number of custom Grid Engine submission scripts (conveniently prepended
with "qsub") that will invoke bang.sh on your behalf as a task array are
also provided.

You should **really** just use bang.sh for all of testing.  In addition to
being easier to use, the summary CSV data it produces can be visualized by
using "create_plots-Axb_random.py." Please see below for some guidance on
how to use this tool in conjunction with bang.sh testing.

## Requirements and Prerequisites

Axb_random.py requires python-3.6 or greater along with the numpy and scipy
libraries to function properly.

PNG plot generation requires tailhead, matplotlib, and pandas.

To install the Linux system packages:

```
$ sudo yum install -y python36
$ sudo pip-3.6 install numpy scipy matplotlib installpandas tailhead
$ which python3
/usr/bin/python3
```
 
OS X users are strongly advised to use Homebrew to deploy the required tools:

```
$ /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
$ brew doctor
$ brew install ansible git packer parallel python3 terraform
$ pip3 install matplotlib numpy pandas requests seaborn scipy tailhead
```

## Usage Guidelines

Please run Axb_random.py with the -h flag to see all available options:

```
$ ./Axb_random.py -h
usage: Axb_random.py [-h] --jobid JOBID --matrix-size MATRIX_SIZE
                     [--console-dump CONSOLE_DUMP] [--create-csv CREATE_CSV]
                     [--create-logs CREATE_LOGS] [--note NOTE]

Measures system performance by solving Ax=b using matrices and standard
deviations generated from the normal distribution

optional arguments:
  -h, --help            show this help message and exit
  --jobid JOBID, -J JOBID
                        name of the job - used to determine CONSOLE_DUMP,
                        CONSOLE_LOG, and CSV_DATA file names
  --matrix-size MATRIX_SIZE, -M MATRIX_SIZE
                        set dimensions of the square matrix A
  --console-dump CONSOLE_DUMP, -D CONSOLE_DUMP
                        print A, b, x, and all sigma values to stdout
  --create-csv CREATE_CSV, -C CREATE_CSV
                        create a CSV data file for offline analysis
  --create-logs CREATE_LOGS, -L CREATE_LOGS
                        print console_dump output to a text file
  --note NOTE, -N NOTE  short description of the test to be included as a
                        field in the CSV data file - do **not** use commas with
                        this option
```

An example CSV file output looks like this:

```
ip-172-31-91-135,2,11000,299.8823,ebs_m5large,995209173,379691268,229.5013
```

The suggested methods of running this suite on a cluster are documented in
qsub-Axb_random.sh, a submission script provided for the user's convenience.

Please take note that Axb_random.py will **not** run if these three optional 
paramters (console-dump, create-csv, and create-logs) are disabled; there
isn't any point to running the test if no useful data is being generated. 

**Mixed Workloads.** To evaluate "mixed" workloads that are probably a better
simulation of a typical workload on a shared HPC cluster, use bang.sh with
a good mix of varying MATRIX_SIZE values, following the guidelines outlined
in qsub-Axb_random.sh.  Random MATRIX_SIZE values could also be used to 
simulate a heterogeneous HPC load.

Overall, bang.sh is far better suited for handling multiple interations and
generating data to be analyzed with customers.  It also aggregates all CSV
data into a single summary file for offline analysis that can be plotted by
the included plot creation scripts.

Please see below for an important note on memory requirements when computing
larger matrices. Also, please be mindful of the amount of time and the costs
that are associated with running larger tests; building a 4,000-core cluster
stack is probably overkill when seeking to obtain useful performance data.

## Effects of Invoking Axb_random.py with Different Options

There can be significant differences in elapsed time when the HPC operator
invokes the console-dump and/or create-logs options, especially with larger
matrices.  This provides flexibility with creating scenarios that will stress
memory, CPU, and disk performance.

It is **critical** that the operator reports options that were called when
submitting performance data to ensure that any apples-to-apples comparisons
remain valid.

For rough comparative purposes, the following results were generated using a
2017 Apple Macbook Pro with a 2.5 GHz Intel Core i7 CPU, 16 GB of physical
memory, and a 500GB flash drive running OSX 10.12.6 (Sierra):

###console-dump=yes, create-log=yes, create-csv=yes
```
$ ./Axb_random.py --jobid=foo --matrix-size=N -D yes -C yes -L yes
	N=  512		t =    1.7142 seconds
	N= 1024		t =    9.1533 seconds
	N= 2048		t =   59.9226 seconds
	N= 4096		t =  408.8307 seconds
	N= 8192		t = 2595.5627 seconds
```

###console-dump=yes, create-log=no, create-csv=yes
```
$ ./Axb_random.py --jobid=foo --matrix-size=N -D yes -C yes -L no
	N=  512		t =    0.9918 seconds
	N= 1024		t =    4.8456 seconds
	N= 2048		t =   30.3101 seconds
	N= 4096		t =  208.8762 seconds
	N= 8192		t = 1330.3521 seconds
```

###console-dump=no, create-log=yes, create-csv=yes
```
$ ./Axb_random.py --jobid=foo --matrix-size=N -D no -C yes -L yes
	N=  512		t =    0.8234 seconds		log_size =   2 MB
	N= 1024		t =    4.1622 seconds		log_size =   8 MB
	N= 2048		t =   25.9101 seconds		log_size =  33 MB
	N= 4096		t =  186.7692 seconds		log_size = 132 MB
	N= 8192		t = 1229.3696 seconds		log_size = 526 MB
```

###console-dump=no, create-log=no, create-csv=yes
```
$ ./Axb_random.py --jobid=foo --matrix-size=N -D no -C yes -L no
	N=  512		t =  0.1665 seconds
	N= 1024		t =  0.1761 seconds
	N= 2048		t =  0.5791 seconds
	N= 4096		t =  2.1579 seconds
	N= 8192		t = 10.7420 seconds
```

Some general recommendations to get the most out from this testing suite:

* When running in standalone i.e. localhost mode, use GNU screen (usually
included by default with Linux) or byobu (http://byobu.co) to prevent SSH
session timeouts from disrupting the test.

* Computing large matrices requires lots of memory. When running tests with
N > 8000, it is strongly recommended that the host of interest have at least
16GB of memory available (t2.xlarge is a good starting point).

* Running the testing suite against different EC2 instance types is a good
way for new AWS users to get acclimated to the AWS compute offerings.

* Generating just the CSV data file allows eliminates I/O which permits the
operator to calculate solutions for very large matrix sizes, which we
feel is a good test of CPU performance. The Macbook used to generate the data
above was able to handle up to N=45000 before running into resoure limits.
Unmodified EC2 instances will only have about 5GB of useful space, so please
be mindful of this when generating log files.

Example:

```
$ ./Axb_random.py --jobid=BigMatrix --matrix-size=42420 --console-dump=no --create-csv=yes --create-logs=no --note "Massive matrix testing"
```

* Using smaller matrices (N < 2000) on an HPC cluster is a good test of the
scheduler's ability to spool small jobs. These smaller N values can also be
used to highlight network performance issues between different types of EC2
networking interfaces. 

* Collecting a log file and dumping to console output takes about twice as
long as collecting just a log file or dumping to console. 

* Collecting just a log file (that is, not dumping anything to the console)
is consistenly faster than dumping to console without capturing a log file,
especially when N is larger.  More investigation is needed but a preliminary
hypothesis is that the OS can address the file system faster than the display.
Further testing against instances with beefier display capabilities is on tap
for the future.

* The performance differences between each test type will increase with N (see
the data above).

* Generating log files is probably better for evaluating overall system
performance testing than dumping to the console. The latter could be
throttled by system display buffer capacity; as a to-do, further invesigation
using instances with beefier video capabilities is planned.

Please plan accordingly when devising your performance testing strategy.
