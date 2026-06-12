"""
tests/conftest.py
Configuração global do pytest para MiniPar v2025.2.
"""
import sys
import os

# Garante que src/ esteja no path para todos os testes
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
