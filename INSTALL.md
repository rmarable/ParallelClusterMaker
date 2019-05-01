# INSTALL.md

## Introduction

ParallelClusterMaker is Open Source software that simplifies the automation
of creating, deleting, and administering AWS ParallelCluster stacks.

The recommended way to use ParallelClusterMaker is to stand up a dedicated
jumphost using the code in ParallelClusterMaker/JumphostMaker, and from this
dedicated jumphost, run the code in ParallelClusterMaker/ClusterMaker to
build and administer ParallelCluster stacks.

Guidance is also provided for launching new ParallelCluster stacks using
the code in ParallelClusterMaker/ClusterMaker directly from OSX.  Please be 
forewarned that this method requires installing Homebrew and may cause other
unforeseen problems with your local environment.

In theory, this toolkit can also be used on Windows machines, but this method
has not been tested and will **not** be supported.

## Using a Dedicated EC2 Jumphost

The recommended way to leverage this code is to create a dedicated jumphost
to launch ParallelCluster stacks.

* Create a 'src' directory and clone the ParallelCluster Git repo to your local
environment:

```
$ cd ~
$ mkdir src
$ cd src
$ git clone https://github.com/rmarable/ParallelClusterMaker.git
```

* Install Terraform using the guidance provided here:

https://www.terraform.io/downloads.html

* Create and activate a virtual Python3 environment called "pcluster-jumphost"
to support the jumphost installation with either pyenv (recommended) or
virtualenv.  For more information on these virtulizers, please visit:

pyenv: homehttps://github.com/pyenv/pyenv#installation
virtualenv: https://virtualenv.pypa.io/en/latest/installation/

```
$ pyenv activate pclusterdev
```

* Use pip to install the required Python libraries for building the jumphost:

```
$ cd ~/src/ParallelClusterMaker/JumphostMaker
$ pip install requirements.pcluster_jumphost.txt.
```

Build the jumphost using the "make-pcluster-jumphost.py" script, substituting
appropriate values for your use case:

```
$ ./make-pcluster-jumphost.py -A us-east-2c -N jumphost20190420 -O rmarable -E rodney.marable@gmail.com
```

When the jumphost becomes available, login and change to the ClusterMaker
source code directory:

```
$ ./access_jumphost -N jumphost20190420
$ cd ParallelClusterMaker/ClusterMaker
```

* You are now ready to build ParallelCluster stacks.  Please consult README.md
for more detailed information on leveraging the scripts in this toolkit.

## Building an Installation Environment on OSX

Please be forewarned that this method is **not** recommended, requires more
work, and may seriously damage your local OSX environment.

"Play at your own risk!"
  -- Planet Patrol

* Clone the ParallelClusterMaker toolkit into your local ~/src directory:

```
$ mkdir ~/src
$ cd ~/src
$ git clone https://github.com/rmarable/ParallelClusterMaker.git
```

* Install Homebrew (OSX users only):

```
$ /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
```

* Use Homebrew to install some other critical applications:

```
$ brew install ansible autoconf automake gcc jq libtool make readline
```

* Install Docker using the guidance provided here:

https://docs.docker.com/docker-for-mac/install/

* Install Python3 using the guidance provided here:

https://realpython.com/installing-python/

* Configure the AWS CLI according to the guidelines provided in the AWS public
documentation:

https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html

* Install and activate a virtual Python environment using virtualenv or pyenv.
Please visit "https://docs.python-guide.org/dev/virtualenvs/" for more details
on Python virtual envirionments, something everyone should be using.

pyenv is cleaner and preferred, but it doesn't support using prompt that will
support displaying the current Python version like virtualenv does without
some additional steps.  Please follow the installation guidelines provided
here: https://github.com/pyenv/pyenv#installation

Please be **very** careful or you may inadvertedly damage your local Python
environment:

```
$ brew install pyenv
$ brew install pyenv-virtualenv
$ pyenv version 3.7.2
$ pyenv virtualenv parallelclustermaker
$ pyenv activate parallelclustermaker
```

virtualenv should **not** be installed in the ParallelClusterMaker source
folder to help keep the source tree clean and organized:

```
$ pip install virtualenv
$ virtualenv --version
16.1.0
$ mkdir -p ~/src/parallelclustermaker
$ virtualenv -p /usr/local/bin/python3.7 ~/src/parallelclustermaker
$ export VIRTUALENVWRAPPER_PYTHON=/usr/local/bin/python3.7
$ source ~/src/parallelclustermaker/bin/activate
```

* Install the required Python libraries into the Python virtual environment
using the included requirements.txt files:

```
$ cd ParallelClusterMaker/JumphostMaker
$ pip install -r requirements.pcluster-jumphost.txt
$ cd ../ClusterMaker
$ pip install -r requirements.ParallelClusterMaker.txt
$ cd ~
```

* Install Node.js: http://tiny.amazon.com/i0txlrlo/docsawsamazsdkfv2devesett

```
$ curl -o- https://raw.githubusercontent.com/creationix/nvm/v0.32.0/install.sh | bash
$ export NVM_DIR="~/.nvm"
$ [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
$ nvm install 10.15.3
```

* Install the Serverless Toolkit following the guidelines provided here:
https://serverless.com/framework/docs/providers/aws/guide/installation/

```
$ npm install -g serverless
```

* In the AWS Management Console, apply a formal name to the VPC(s) within any
region you wish to deploy cluster stacks by navigating to:

```
Console / VPC / Your VPCs
```
Edit the Name field as desired.  For example, use "nova" for us-east-1, "cleveland" for us-east-2, "dublin" for eu-west-1, and so forth.

* You are now ready to build ParallelCluster stacks.  Please consult README.md
for more detailed information on leveraging the scripts in this toolkit.

## About make-pcluster.py

To view all available options for make-pcluster.py:

```
$ cd ParallelClusterMaker/ClusterMaker
$ ./make-pcluster.py --help
```

## Example ParallelCluster Stack Build

* This example builds a small Grid Engine cluster running Amazon Linux 2 using
c5.xlarge instances running Amazon Linux 2.  This command can be run from a
jumphost or a properly configured OSX environment, substituting where needed
to match your use case:

```
$ cd ~/src/ParallelClusterMaker/ClusterMaker
$ ./make-pcluster -N test01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a --master_instance_type=c5.xlarge --compute_instance_type=c5.xlarge --max_queue_size=4 --base_os=alinux2
```

* The build process will take about 30 minutes to complete.  Once the cluster
becomes available, login to the head node via SSH:

```
$ ./access_cluster.py --cluster_name="rmarable-test01" -A us-east-1a
```

* Compute away using the normal commands associated with your choice of
scheduler.

* To manually delete the cluster from your local environment:

```
$ ./kill-pcluster.py -N test01 -O rmarable -A us-east-1a
```

* Once you are done working with the cluster, disable the virtual Python
environment:

```
$ pyenv deactivate
```

If you are using virtualenv:

```
$ deactivate
```

Please consult the README.md file for additional information on how to use the
make-pcluster.py script and its companion tools.

## Reporting Bugs

Any bugs, issues, or otherwise unexpected behavior should be reported to
Rodney Marable <rodney.marable@gmail.com> using the normal Github channels.  
Pull requests for additional functionality are always welcome.
