#!/usr/bin/env python3
#
################################################################################
# Name:         print_fibonacci.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   May 6, 2018
# Last Changed: August 8, 2018
# Purpose:      Print Fibonacci numbers to stdout using index (from 1 to N) or
#		the length in digits of the computed value
# Notes:	Using the shell to filter brackets from the script output is
#		faster than Python regex.  To use, replace regex.replace with:
#		./fibonacci.py | tr -d '[' | tr -d ']'
################################################################################

import argparse
import re
import sys

# Define a function to compute Fibonacci numbers.

def compute_fibonacci(p):
    fibonacci_array = []
    m = 0
    n = 1
    for i in range(1, p):
        fibonacci_array.append(n)
        m, n = n, m+n
    return fibonacci_array

# Define a function to properly handle the case of N=1.

def handle_number_one():
    print('Current Index: 1')
    print('Number of Digits: 1')
    print('Computed Value: 1')
    print('')

# Start the main script here.

# Configure the parser.

parser = argparse.ArgumentParser(description='Print Fibonacci numbers by number of digits or Fibonacci_index')
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--enable_index', action='store_true')
group.add_argument('--enable_digits', action='store_true')

# Set output file and compression arguments.

parser.add_argument('--index', '-I', help='Compute Fibonacci numbers from 1 to index (default = 10000)', required=False, type=int, default=10000)
parser.add_argument('--digits', '-D', help='Compute Fibonacci numbers up to D digits long (default = 1000)', required=False, type=int, default=1000)

args = parser.parse_args()
enable_index = args.enable_index
enable_digits = args.enable_digits
digits = args.digits
index = args.index

if enable_index:
    if index == 1:
        handle_number_one()
    else:
        for index in range(2, index+2):
            print("Current Index:", index-1)
            result = compute_fibonacci(index)[-1:]
            FibNum = ','.join( str(result) for e in result ).replace('[', '').replace(']', '')
            print("Number of Digits:", len(FibNum))
            print("Computed Value:", FibNum)
            print('')

if enable_digits:
    index = 1
    FibNum = 1
    if digits == 1:
        handle_number_one()
    while len(str(FibNum)) < digits:
        if index == 1:
            handle_number_one()
        else:
            print("Current Index:", index)
            result = compute_fibonacci(index)[-1:]
            FibNum = ','.join( str(result) for e in result ).replace('[', '').replace(']', '')
            print("Number of Digits:", len(FibNum))
            print("Computed Value:", FibNum)
            print('')
        index += 1
