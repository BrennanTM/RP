#!/usr/bin/env python3
"""Run the adaptive processor in continuous mode"""
import sys
import time
from adaptive_processor import main

# Simulate selecting option 4 (continuous mode)
class ContinuousInput:
    def __init__(self):
        self.returned_4 = False
        
    def __call__(self, prompt):
        if not self.returned_4:
            self.returned_4 = True
            return '4'
        # Keep running
        time.sleep(60)
        return ''

# Replace input with our automated version
import builtins
builtins.input = ContinuousInput()

# Run the main program
if __name__ == "__main__":
    main()
