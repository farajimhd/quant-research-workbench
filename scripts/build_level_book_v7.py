"""Build split-aware historical V7 books and prepare a causal test session."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research.level_book.v7.build import main

if __name__=='__main__':main()
