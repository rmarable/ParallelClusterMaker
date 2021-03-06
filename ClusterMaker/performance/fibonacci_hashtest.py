#!/usr/bin/env python3
#
################################################################################
# Name:		fibonacci_hashtest.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 25, 2018
# Last Changed:	August 8, 2018
# Purpose:	Generate Fibonacci numbers and hash them against randomly
#		generated salts
################################################################################

# Start the job timer.

import time
start_time=time.time()
datestamp = time.strftime("%S%M%H%d%m%Y")

# Import the rest of the required Python libraries.

import argparse
import binascii
import gzip
import hashlib
import os
import shutil
import string
import subprocess
import uuid

# Configure the parser.

parser = argparse.ArgumentParser(description='Generate Fibonacci numbers and hash them against randomly generated salts')
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--enable_digits', action='store_true')
group.add_argument('--enable_index', action='store_true')

# Set output file and compression arguments.

parser.add_argument('--archive_path', '-A', help='Path to the archive directory for storing compressed output files (default = ./archive_fibonacci_hash)', required=False, default='./archive_fibonacci_hash')
parser.add_argument('--compression_processes', '-P', help='Number of CPUs to use for compression', required=False, type=int, default=8)
parser.add_argument('--compression_prog', '-C', help='Set the compression program to gzip or pigz (default = pigz)', required=False, default='pigz')
parser.add_argument('--outfile', '-O', help='Name of the output file (default = stdout)', required=False)

parser.add_argument('--digits', '-D', help='Compute hashes for Fibonacci numbers up to D digits long (default = 1000)', required=False, type=int, default=1000)
parser.add_argument('--index', '-I', help='Compute hashes for Fibonacci numbers from 1 to index (default = 10000)', required=False, type=int, default=10000)

# Set values for critical parameters based on command line input.

args = parser.parse_args()
archive_path = args.archive_path
compression_processes = args.compression_processes
compression_prog = args.compression_prog
if args.outfile:
    outfile = args.outfile + '.' + datestamp
enable_index = args.enable_index
enable_digits = args.enable_digits
digits = args.digits
index = args.index

# Function: compute_test_time(t)
# Purpose: define a function to compute the elapsed test time.

def compute_test_time(t):
    end_time=t
    elapsed_time=round(end_time-start_time,4)
    if args.outfile:
        print("--------------------------------------------------------------------------------", file=open(outfile + '.fibonacci_dump', "a"))
        print('',  file=open(outfile + '.fibonacci_dump', "a"))
        print('archive_path = ' + archive_path + '/' + outfile + '.fibonacci_dump.gz', file=open(outfile  + '.fibonacci_dump', "a"))
        print("time_elapsed = %.4f seconds  " % elapsed_time, file=open(outfile  + '.fibonacci_dump', "a"))
    else:
        print('')
        print("time_elapsed  = %.4f seconds  " % elapsed_time)

# Function: compute_fibonacci(p)
# Purpose: define a function to compute Fibonacci numbers.

def compute_fibonacci(p):
    fibonacci_array = []
    m = 0
    n = 1
    for i in range(p):
        fibonacci_array.append(n)
        m, n = n, m+n
    return fibonacci_array

# Function: handle_number_one()
# Purpose: properly handle the case of N=1.

def handle_number_one():
    salt = uuid.uuid4().hex
    FibNumOne = str.encode(str(1))
    hash_string = hashlib.blake2b(salt.encode() + FibNumOne).hexdigest()
    if args.outfile:
        print ('  n         = 1', file=open(outfile + '.fibonacci_dump', "a"))
        print ('F(n) digits = 1', file=open(outfile + '.fibonacci_dump', "a"))
        print ("random salt =", "{", salt, "}", file=open(outfile + '.fibonacci_dump', "a"))
        print ("hash string =", "{", hash_string, "}", file=open(outfile + '.fibonacci_dump', "a"))
        print ("F(n) value  = 1", file=open(outfile + '.fibonacci_dump', "a"))
        print ("--------------------------------------------------------------------------------", file=open(outfile + '.fibonacci_dump', "a"))
    else:
        print ('  n         = 1')
        print ('F(n) digits = 1')
        print ("random salt =", "{", salt, "}")
        print ("hash string =", "{", hash_string, "}")
        print ('F(n) value  = 1')
        print ("--------------------------------------------------------------------------------")

# Function: print_output()
# Purpose: print output to console and/or outfile.

def print_output():
    if args.outfile:
        print ("  n         =", index, file=open(outfile + '.fibonacci_dump', "a"))
        print ("F(n) digits =", len(FibNum), "digits", file=open(outfile + '.fibonacci_dump', "a"))
        print ("random salt =", "{", salt, "}", file=open(outfile + '.fibonacci_dump', "a"))
        print ("hash string =", "{", hash_string, "}", file=open(outfile + '.fibonacci_dump', "a"))
        print ("F(n) value  =", FibNum.decode('utf-8'), file=open(outfile + '.fibonacci_dump', "a"))
        print ("--------------------------------------------------------------------------------", file=open(outfile + '.fibonacci_dump', "a"))
    else:
        print ("  n         =", index)
        print ("F(n) digits =", len(FibNum), "digits")
        print ("random salt =", "{", salt, "}")
        print ("hash string =", "{", hash_string, "}")
        print ("F(n) value  =", FibNum.decode('utf-8'))
        print ("--------------------------------------------------------------------------------")

# Delete any pre-existing output files to prevent clashing.

if args.outfile:
    if os.path.isfile(archive_path + '/' + outfile + '.fibonacci_dump.gz'):
        os.remove(archive_path + '/' + outfile + '.fibonacci_dump.gz')
        open(outfile + '.fibonacci_dump', 'w')

# Compute a Fibonacci number and hash against a randomly generated salt.
# Allow the operator to select between:
# 	--enable_index (compute Fibonacci hashes from 1 to "--index")
# 	--enable_digits (compute hashes until len(result) > "--digits")
# Print the index, computed Fibonacci number, number of digits in the computed
# Fibonacci number, random salt, and hash result to the console.
# Dump the results to outfile if "--outfile" is enabled.

if enable_index:
    if index == 1:
        handle_number_one()
    else:
        for index in range(1, index+1):
            result = compute_fibonacci(index)[-1:]
            result_string = ','.join( str(result) for e in result ).replace('[', '').replace(']', '')
            FibNum = str.encode(result_string)
            salt = uuid.uuid4().hex
            hash_string = hashlib.blake2b(salt.encode() + FibNum).hexdigest()
            print_output()

if enable_digits:
    index = 1
    result_string = 1
    if digits == 1:
        handle_number_one()
    while len(str(result_string)) < digits:
        if index == 1:
            handle_number_one()
        else:
            result = compute_fibonacci(index)[-1:]
            result_string = ','.join( str(result) for e in result ).replace('[', '').replace(']', '')
            FibNum = str.encode(result_string)
            salt = uuid.uuid4().hex
            hash_string = hashlib.blake2b(salt.encode() + FibNum).hexdigest()
            print_output()
        index += 1

# Compute the elapsed test time.
# Print the result to the console and append to outfile (if defined).

compute_test_time(time.time())

# Compress and move the output file to archive_path.

if args.outfile:
    if not os.path.isdir(archive_path):
        os.makedirs(archive_path)
    files_to_compress = [ outfile + '.fibonacci_dump' ]
    for compfile in files_to_compress:
        try:
            with open(compfile) as file:
                if (compression_prog) == "pigz":
                    subprocess.run(["pigz", "-f", "--processes", str(compression_processes), compfile])
                if (compression_prog) == "gzip":
                    f_in=open(compfile, 'rb')
                    f_out=gzip.open(compfile + '.gz', 'wb')
                    shutil.copyfileobj(f_in, f_out)
        except IOError:
            pass
    for compfile in files_to_compress:
        shutil.move(compfile + '.gz', archive_path + '/' + compfile + '.gz')
