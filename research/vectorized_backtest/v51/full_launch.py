"""Prepare V51 by rescoring the exact original V50 founder receipts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from .initial_rescore import main

if __name__=='__main__':main()
