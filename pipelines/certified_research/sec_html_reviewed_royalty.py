"""Reviewed royalty-trust native measures; printed dollar symbol is not ISO USD.

Source-bound cash, assets, royalty/net-profits receipts and distributable income.
No GAAP equivalence, zero imputation, currency conversion or family addition.
"""
from copy import deepcopy

REVIEWED = {'8235efec97fd6611048c343c489b182a23ede2e8ebe03acda6933daa7d50ea3a': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': '8235efec97fd6611048c343c489b182a23ede2e8ebe03acda6933daa7d50ea3a',
                                                                      'cik': '0001371782',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_3a7ba76ff35262c734bea028a2c55fb04404d2e2b7ca5e77ff22189c5c3b1487',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '3a7ba76ff35262c734bea028a2c55fb04404d2e2b7ca5e77ff22189c5c3b1487',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_3a7ba76ff35262c734bea028a2c55fb04404d2e2b7ca5e77ff22189c5c3b1487'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1082681',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_3a7ba76ff35262c734bea028a2c55fb04404d2e2b7ca5e77ff22189c5c3b1487',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '3a7ba76ff35262c734bea028a2c55fb04404d2e2b7ca5e77ff22189c5c3b1487',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_3a7ba76ff35262c734bea028a2c55fb04404d2e2b7ca5e77ff22189c5c3b1487'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1168106',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_23699a66c6fb80716c21d9fab0b9cdcfadb188a091067307d46819c75ebe40d6',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '23699a66c6fb80716c21d9fab0b9cdcfadb188a091067307d46819c75ebe40d6',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_23699a66c6fb80716c21d9fab0b9cdcfadb188a091067307d46819c75ebe40d6'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1832389',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_23699a66c6fb80716c21d9fab0b9cdcfadb188a091067307d46819c75ebe40d6',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '23699a66c6fb80716c21d9fab0b9cdcfadb188a091067307d46819c75ebe40d6',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_23699a66c6fb80716c21d9fab0b9cdcfadb188a091067307d46819c75ebe40d6'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2260841',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_579aa4ca3f27ceed5d5daaf199d54642e0b7b503fdb79a5d6fb0361832ab8b06',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '579aa4ca3f27ceed5d5daaf199d54642e0b7b503fdb79a5d6fb0361832ab8b06',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_579aa4ca3f27ceed5d5daaf199d54642e0b7b503fdb79a5d6fb0361832ab8b06'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1836437',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_579aa4ca3f27ceed5d5daaf199d54642e0b7b503fdb79a5d6fb0361832ab8b06',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '579aa4ca3f27ceed5d5daaf199d54642e0b7b503fdb79a5d6fb0361832ab8b06',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_579aa4ca3f27ceed5d5daaf199d54642e0b7b503fdb79a5d6fb0361832ab8b06'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2992742',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_661b093ff1ee60a77e370a70619e0aa192abb0f987bbf80c432470dec671cdc2',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '661b093ff1ee60a77e370a70619e0aa192abb0f987bbf80c432470dec671cdc2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_661b093ff1ee60a77e370a70619e0aa192abb0f987bbf80c432470dec671cdc2'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1610000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_661b093ff1ee60a77e370a70619e0aa192abb0f987bbf80c432470dec671cdc2',
                                                                                 'native': {'issuer_cik': '0001371782',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '661b093ff1ee60a77e370a70619e0aa192abb0f987bbf80c432470dec671cdc2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001371782}N_661b093ff1ee60a77e370a70619e0aa192abb0f987bbf80c432470dec671cdc2'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2760000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 6}}],
                                                                      'plan_hash': 'a299e53254c684abcf3b33d29e59f5ece93575687405ef468831b11a2c54c41a'},
 'a86f60625bf76a07dd0bf881c2247dac8cb5438176d8e61f75c57ec1cbb94816': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': 'a86f60625bf76a07dd0bf881c2247dac8cb5438176d8e61f75c57ec1cbb94816',
                                                                      'cik': '0001505413',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_3907f15de820f67632f65b7d0a04aa476550eaaea7d1817cc12faebe6a6fe83f',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '3907f15de820f67632f65b7d0a04aa476550eaaea7d1817cc12faebe6a6fe83f',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_3907f15de820f67632f65b7d0a04aa476550eaaea7d1817cc12faebe6a6fe83f'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1528847',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_3907f15de820f67632f65b7d0a04aa476550eaaea7d1817cc12faebe6a6fe83f',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '3907f15de820f67632f65b7d0a04aa476550eaaea7d1817cc12faebe6a6fe83f',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_3907f15de820f67632f65b7d0a04aa476550eaaea7d1817cc12faebe6a6fe83f'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2026639',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_fbd7bf249573c06cd8a6d3dd68de53443ac2e127388cba968947104651125f42',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'fbd7bf249573c06cd8a6d3dd68de53443ac2e127388cba968947104651125f42',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_fbd7bf249573c06cd8a6d3dd68de53443ac2e127388cba968947104651125f42'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '9069571',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_fbd7bf249573c06cd8a6d3dd68de53443ac2e127388cba968947104651125f42',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'fbd7bf249573c06cd8a6d3dd68de53443ac2e127388cba968947104651125f42',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_fbd7bf249573c06cd8a6d3dd68de53443ac2e127388cba968947104651125f42'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '10399993',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['STATEMENTS OF ASSETS '
                                                                                              'AND TRUST CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF ASSETS '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1700695',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2484950',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [6]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 5}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3286819',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_2875541ce9e66930012b7be18beb291e791149e7b04427cc9840a196fdca600d'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '4383770',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [14]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 13}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': 'eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1615000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': 'eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2210000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [6]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 5}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': 'eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3145000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                 'native': {'issuer_cik': '0001505413',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': 'eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001505413}N_eb53084ad4762ad59e563deaa9945aeec3f02275699249608a7f4a86ec751459'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3655000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'months '
                                                                                                               'ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 4,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [14]},
                                                                                 'unit_locator': {'table': 4,
                                                                                                  'row': 2,
                                                                                                  'column': 13}}],
                                                                      'plan_hash': '00a8659e1f9e5a734d3eb6e9d7baff9e64b1ef7407c4164d9bb5ba8ae5665027'},
 '3d58a58c98c56dd5252510b9d31cfdf2de21428833d23a60765b93d65d62de66': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': '3d58a58c98c56dd5252510b9d31cfdf2de21428833d23a60765b93d65d62de66',
                                                                      'cik': '0001724009',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_5915e8c939d672e055b7fe4e152338760e61fc1e0d7011859c95338098596f95',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Cash and '
                                                                                                     'short-term '
                                                                                                     'investments',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '5915e8c939d672e055b7fe4e152338760e61fc1e0d7011859c95338098596f95',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_5915e8c939d672e055b7fe4e152338760e61fc1e0d7011859c95338098596f95'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1370880',
                                                                                 'label': 'Cash and short-term '
                                                                                          'investments',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_5915e8c939d672e055b7fe4e152338760e61fc1e0d7011859c95338098596f95',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Cash and '
                                                                                                     'short-term '
                                                                                                     'investments',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '5915e8c939d672e055b7fe4e152338760e61fc1e0d7011859c95338098596f95',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_5915e8c939d672e055b7fe4e152338760e61fc1e0d7011859c95338098596f95'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1199573',
                                                                                 'label': 'Cash and short-term '
                                                                                          'investments',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_76f323a8b96258203949f376764193d981c14c4394f32ac7c06dc9deae85c17d',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'TOTAL ASSETS',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '76f323a8b96258203949f376764193d981c14c4394f32ac7c06dc9deae85c17d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_76f323a8b96258203949f376764193d981c14c4394f32ac7c06dc9deae85c17d'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '27421444',
                                                                                 'label': 'TOTAL ASSETS',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_76f323a8b96258203949f376764193d981c14c4394f32ac7c06dc9deae85c17d',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'TOTAL ASSETS',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '76f323a8b96258203949f376764193d981c14c4394f32ac7c06dc9deae85c17d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_76f323a8b96258203949f376764193d981c14c4394f32ac7c06dc9deae85c17d'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '27795448',
                                                                                 'label': 'TOTAL ASSETS',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Net profits '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '697904',
                                                                                 'label': 'Net profits income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Net profits '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1545465',
                                                                                 'label': 'Net profits income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Net profits '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1345337',
                                                                                 'label': 'Net profits income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Net profits '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_a03f66451c0a4645cf8f1bc095971d7807a995a498dc4e03cdd3a944c0dbb5e2'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3256228',
                                                                                 'label': 'Net profits income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [14]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 13}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '409050',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 8,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1200518',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 8,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '813117',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 8,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                 'native': {'issuer_cik': '0001724009',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001724009}N_4f448a9e9b61fec4dce55683381ce377f0ee03ff3f9cd71a907ee14331dbcc0d'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2666713',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 8,
                                                                                             'label_column': 0,
                                                                                             'columns': [14]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 3,
                                                                                                  'column': 13}}],
                                                                      'plan_hash': '867d5647a94b4497d92120bca5915e086a36f9e16f49fa86b2886cf9ee6dfceb'},
 '0f6f7dcbf44049b2824ca486af12437e87385bf0c2f9c2e772a02493e0180d64': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': '0f6f7dcbf44049b2824ca486af12437e87385bf0c2f9c2e772a02493e0180d64',
                                                                      'cik': '0000710752',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_15948c58c23747936f837f7a808ae5de3a647c6eea4c1dd96f6576d5e1362e21',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Cash and '
                                                                                                     'short-term '
                                                                                                     'investments',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES, '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '15948c58c23747936f837f7a808ae5de3a647c6eea4c1dd96f6576d5e1362e21',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_15948c58c23747936f837f7a808ae5de3a647c6eea4c1dd96f6576d5e1362e21'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '9682365',
                                                                                 'label': 'Cash and short-term '
                                                                                          'investments',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES, AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES, '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [6]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 5,
                                                                                                  'column': 5}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_15948c58c23747936f837f7a808ae5de3a647c6eea4c1dd96f6576d5e1362e21',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Cash and '
                                                                                                     'short-term '
                                                                                                     'investments',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES, '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '15948c58c23747936f837f7a808ae5de3a647c6eea4c1dd96f6576d5e1362e21',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_15948c58c23747936f837f7a808ae5de3a647c6eea4c1dd96f6576d5e1362e21'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '7574257',
                                                                                 'label': 'Cash and short-term '
                                                                                          'investments',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES, AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES, '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 5,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_711f03e843c6ac7c6753efe679d7bc1e37768869f4d855028833c44634b1d958',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Total Assets',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES, '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '711f03e843c6ac7c6753efe679d7bc1e37768869f4d855028833c44634b1d958',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_711f03e843c6ac7c6753efe679d7bc1e37768869f4d855028833c44634b1d958'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '9747347',
                                                                                 'label': 'Total Assets',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES, AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES, '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 7,
                                                                                             'label_column': 0,
                                                                                             'columns': [6]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 5,
                                                                                                  'column': 5}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_711f03e843c6ac7c6753efe679d7bc1e37768869f4d855028833c44634b1d958',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Total Assets',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES, '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '711f03e843c6ac7c6753efe679d7bc1e37768869f4d855028833c44634b1d958',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_711f03e843c6ac7c6753efe679d7bc1e37768869f4d855028833c44634b1d958'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '7643865',
                                                                                 'label': 'Total Assets',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES, AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES, '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 7,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 5,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_7832d59026ba6c4d2b87c19790d95b4dd2da1aa567068e70465068874dd510d7',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Total '
                                                                                                     'liabilities',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES, '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '7832d59026ba6c4d2b87c19790d95b4dd2da1aa567068e70465068874dd510d7',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_7832d59026ba6c4d2b87c19790d95b4dd2da1aa567068e70465068874dd510d7'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1335633',
                                                                                 'label': 'Total liabilities',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES, AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES, '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 11,
                                                                                             'label_column': 0,
                                                                                             'columns': [6]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 5,
                                                                                                  'column': 5}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_7832d59026ba6c4d2b87c19790d95b4dd2da1aa567068e70465068874dd510d7',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Total '
                                                                                                     'liabilities',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES, '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '7832d59026ba6c4d2b87c19790d95b4dd2da1aa567068e70465068874dd510d7',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_7832d59026ba6c4d2b87c19790d95b4dd2da1aa567068e70465068874dd510d7'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '796286',
                                                                                 'label': 'Total liabilities',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES, AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES, '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 11,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 5,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_2841a5b753b9285805b98973a3c3d8270d07e28c4151692340454ed3051e81dc',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Royalty income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '2841a5b753b9285805b98973a3c3d8270d07e28c4151692340454ed3051e81dc',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_2841a5b753b9285805b98973a3c3d8270d07e28c4151692340454ed3051e81dc'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '35881186',
                                                                                 'label': 'Royalty income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Notes',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [5]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 3,
                                                                                                  'column': 4}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_2841a5b753b9285805b98973a3c3d8270d07e28c4151692340454ed3051e81dc',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Royalty income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '2841a5b753b9285805b98973a3c3d8270d07e28c4151692340454ed3051e81dc',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_2841a5b753b9285805b98973a3c3d8270d07e28c4151692340454ed3051e81dc'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '37978136',
                                                                                 'label': 'Royalty income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Notes',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [9]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 3,
                                                                                                  'column': 8}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_8dbaeb945493ec1f1a0ca3e5c48135a0c2a79214fbab9fc6f0ebd5136769bef5',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '8dbaeb945493ec1f1a0ca3e5c48135a0c2a79214fbab9fc6f0ebd5136769bef5',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_8dbaeb945493ec1f1a0ca3e5c48135a0c2a79214fbab9fc6f0ebd5136769bef5'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '33891316',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Notes',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 7,
                                                                                             'label_column': 0,
                                                                                             'columns': [5]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 3,
                                                                                                  'column': 4}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_8dbaeb945493ec1f1a0ca3e5c48135a0c2a79214fbab9fc6f0ebd5136769bef5',
                                                                                 'native': {'issuer_cik': '0000710752',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '8dbaeb945493ec1f1a0ca3e5c48135a0c2a79214fbab9fc6f0ebd5136769bef5',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000710752}N_8dbaeb945493ec1f1a0ca3e5c48135a0c2a79214fbab9fc6f0ebd5136769bef5'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '35935478',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Notes',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 7,
                                                                                             'label_column': 0,
                                                                                             'columns': [9]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 3,
                                                                                                  'column': 8}}],
                                                                      'plan_hash': '303452d103611c0341a9e32deac3fc0ae2970f951ea16829f7262d909c999b64'},
 '998374e574432fb621c071cfa113ff72f988f36b15305d8067674765a721dfec': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': '998374e574432fb621c071cfa113ff72f988f36b15305d8067674765a721dfec',
                                                                      'cik': '0001520048',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_fcbe84ace817d7c7e3367736d5a9ffd789d768b6ac46e14980e00aeb2141376a',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'fcbe84ace817d7c7e3367736d5a9ffd789d768b6ac46e14980e00aeb2141376a',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_fcbe84ace817d7c7e3367736d5a9ffd789d768b6ac46e14980e00aeb2141376a'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3346992',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_fcbe84ace817d7c7e3367736d5a9ffd789d768b6ac46e14980e00aeb2141376a',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'fcbe84ace817d7c7e3367736d5a9ffd789d768b6ac46e14980e00aeb2141376a',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_fcbe84ace817d7c7e3367736d5a9ffd789d768b6ac46e14980e00aeb2141376a'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2733791',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_cb4880e679728724728aa7f1c5069b8572c96a80578538870c5af67a10359f8b',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'cb4880e679728724728aa7f1c5069b8572c96a80578538870c5af67a10359f8b',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_cb4880e679728724728aa7f1c5069b8572c96a80578538870c5af67a10359f8b'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '36403816',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_cb4880e679728724728aa7f1c5069b8572c96a80578538870c5af67a10359f8b',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'cb4880e679728724728aa7f1c5069b8572c96a80578538870c5af67a10359f8b',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_cb4880e679728724728aa7f1c5069b8572c96a80578538870c5af67a10359f8b'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '38968032',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': '36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1632084',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': '36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '419589',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': '36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3587391',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [11]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 10}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Income from net '
                                                                                                     'profits interest',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': '36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_36721e0c4bbbdfe75b4d45b773b561dea5637fd7c5bc597053b491ec0344bdc3'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '419589',
                                                                                 'label': 'Income from net profits '
                                                                                          'interest',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [15]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 14}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1122000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '282084',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2541000',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [11]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 10}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                 'native': {'issuer_cik': '0001520048',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'Statements '
                                                                                                               'of '
                                                                                                               'Assets, '
                                                                                                               'Liabilities '
                                                                                                               'and '
                                                                                                               'Trust '
                                                                                                               'Corpus',
                                                                                            'definition_hash': 'c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0001520048}N_c292166975901545156afa9e535922a802a6afb0e4bc63be5a0d19781538c431'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '282084',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['Statements of Assets, '
                                                                                              'Liabilities and Trust '
                                                                                              'Corpus'],
                                                                                 'table_proof': {'heading': 'Statements '
                                                                                                            'of '
                                                                                                            'Assets, '
                                                                                                            'Liabilities '
                                                                                                            'and Trust '
                                                                                                            'Corpus',
                                                                                                 'headers': [[0,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Six '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30,',
                                                                                                               '']],
                                                                                                             [1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 8,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [15]},
                                                                                 'unit_locator': {'table': 8,
                                                                                                  'row': 2,
                                                                                                  'column': 14}}],
                                                                      'plan_hash': '1716f0d8a87abe52d22d64b24bbee00332a99435d3050ce815acce9d85c68a61'},
 '63eda1ceac9012bc00cdcd154b8ff048ada28fef8f3958f44ddfb8567c53a6e4': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': '63eda1ceac9012bc00cdcd154b8ff048ada28fef8f3958f44ddfb8567c53a6e4',
                                                                      'cik': '0000062362',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_76c12f6e67ade0e30dfb056e234c80206cb031d6103efe5907044dc23ce911e6',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '76c12f6e67ade0e30dfb056e234c80206cb031d6103efe5907044dc23ce911e6',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_76c12f6e67ade0e30dfb056e234c80206cb031d6103efe5907044dc23ce911e6'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '940636',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_76c12f6e67ade0e30dfb056e234c80206cb031d6103efe5907044dc23ce911e6',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Cash and cash '
                                                                                                     'equivalents',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '76c12f6e67ade0e30dfb056e234c80206cb031d6103efe5907044dc23ce911e6',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_76c12f6e67ade0e30dfb056e234c80206cb031d6103efe5907044dc23ce911e6'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '921520',
                                                                                 'label': 'Cash and cash equivalents',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_86052f268e6b285031cb6c7ad7514d626a95aecb3f637dff0c0a10790c83d8f2',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '86052f268e6b285031cb6c7ad7514d626a95aecb3f637dff0c0a10790c83d8f2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_86052f268e6b285031cb6c7ad7514d626a95aecb3f637dff0c0a10790c83d8f2'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '940643',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 7,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_86052f268e6b285031cb6c7ad7514d626a95aecb3f637dff0c0a10790c83d8f2',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Total assets',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '86052f268e6b285031cb6c7ad7514d626a95aecb3f637dff0c0a10790c83d8f2',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_86052f268e6b285031cb6c7ad7514d626a95aecb3f637dff0c0a10790c83d8f2'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '921527',
                                                                                 'label': 'Total assets',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'March '
                                                                                                               '31, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 7,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 4,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Oil and natural '
                                                                                                     'gas royalties',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '228173',
                                                                                 'label': 'Oil and natural gas '
                                                                                          'royalties',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Oil and natural '
                                                                                                     'gas royalties',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '332993',
                                                                                 'label': 'Oil and natural gas '
                                                                                          'royalties',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Oil and natural '
                                                                                                     'gas royalties',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181'},
                                                                                 'period_start': '2025-07-01',
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '710069',
                                                                                 'label': 'Oil and natural gas '
                                                                                          'royalties',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Oil and natural '
                                                                                                     'gas royalties',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_56e7f66306c4e0edc24b82f696d56696210da1cdd0668a4c8833380f6668f181'},
                                                                                 'period_start': '2024-07-01',
                                                                                 'period_end': '2025-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '778554',
                                                                                 'label': 'Oil and natural gas '
                                                                                          'royalties',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 4,
                                                                                             'label_column': 0,
                                                                                             'columns': [14]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 13}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35'},
                                                                                 'period_start': '2026-01-01',
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '134148',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 9,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35'},
                                                                                 'period_start': '2025-01-01',
                                                                                 'period_end': '2025-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '242418',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 9,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35'},
                                                                                 'period_start': '2025-07-01',
                                                                                 'period_end': '2026-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '456810',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 9,
                                                                                             'label_column': 0,
                                                                                             'columns': [10]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 9}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                 'native': {'issuer_cik': '0000062362',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'CONDENSED '
                                                                                                               'CONSOLIDATED '
                                                                                                               'STATE '
                                                                                                               'MENTS '
                                                                                                               'OF '
                                                                                                               'DISTRIBUTABLE '
                                                                                                               'INCOME',
                                                                                            'definition_hash': '696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000062362}N_696c33d13bdb70e8e6cb76a4467a5a1aea647944aa3fb860091c01fedd907a35'},
                                                                                 'period_start': '2024-07-01',
                                                                                 'period_end': '2025-03-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '557108',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['CONDENSED CONSOLIDATED '
                                                                                              'STATE MENTS OF '
                                                                                              'DISTRIBUTABLE INCOME'],
                                                                                 'table_proof': {'heading': 'CONDENSED '
                                                                                                            'CONSOLIDATED '
                                                                                                            'STATE '
                                                                                                            'MENTS OF '
                                                                                                            'DISTRIBUTABLE '
                                                                                                            'INCOME',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '',
                                                                                                               'Nine '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'March '
                                                                                                               '31,',
                                                                                                               '']],
                                                                                                             [2,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '',
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 9,
                                                                                             'row': 9,
                                                                                             'label_column': 0,
                                                                                             'columns': [14]},
                                                                                 'unit_locator': {'table': 9,
                                                                                                  'row': 4,
                                                                                                  'column': 13}}],
                                                                      'plan_hash': '361b47bf4380963ae462c59e97b81d19f57d1f5a3d61552c4a03c9af0bc83812'},
 '4ebf3e4ee8e5d4a0ce3ee1b76e567d2f5101169cc76340caee40f3610a2b3eb9': {'version': 'sec-reviewed-html-context-v1',
                                                                      'document_hash': '4ebf3e4ee8e5d4a0ce3ee1b76e567d2f5101169cc76340caee40f3610a2b3eb9',
                                                                      'cik': '0000319654',
                                                                      'scope': 'reviewed_observations',
                                                                      'facts': [{'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_781628ad85749b6426bc47936331921a955e8206b96fae302f1c0fa58395f1ad',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Cash and '
                                                                                                     'short-term '
                                                                                                     'investments',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '781628ad85749b6426bc47936331921a955e8206b96fae302f1c0fa58395f1ad',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_781628ad85749b6426bc47936331921a955e8206b96fae302f1c0fa58395f1ad'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2249988',
                                                                                 'label': 'Cash and short-term '
                                                                                          'investments',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(Unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_781628ad85749b6426bc47936331921a955e8206b96fae302f1c0fa58395f1ad',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Cash and '
                                                                                                     'short-term '
                                                                                                     'investments',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '781628ad85749b6426bc47936331921a955e8206b96fae302f1c0fa58395f1ad',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_781628ad85749b6426bc47936331921a955e8206b96fae302f1c0fa58395f1ad'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1715167',
                                                                                 'label': 'Cash and short-term '
                                                                                          'investments',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(Unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 3,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_62af26c1c84aff709608f403169285b8d83a48e2dc1a0fa2c5514a9f9afa5a6c',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'TOTAL ASSETS',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '62af26c1c84aff709608f403169285b8d83a48e2dc1a0fa2c5514a9f9afa5a6c',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_62af26c1c84aff709608f403169285b8d83a48e2dc1a0fa2c5514a9f9afa5a6c'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2411709',
                                                                                 'label': 'TOTAL ASSETS',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(Unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_62af26c1c84aff709608f403169285b8d83a48e2dc1a0fa2c5514a9f9afa5a6c',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'TOTAL ASSETS',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '62af26c1c84aff709608f403169285b8d83a48e2dc1a0fa2c5514a9f9afa5a6c',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_62af26c1c84aff709608f403169285b8d83a48e2dc1a0fa2c5514a9f9afa5a6c'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1877733',
                                                                                 'label': 'TOTAL ASSETS',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(Unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 5,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_8a9a2aa4d3614593959c8cade3c309d172b72f259a00a2e377a0b9ccb474e4d7',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Total '
                                                                                                     'Liabilities',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '8a9a2aa4d3614593959c8cade3c309d172b72f259a00a2e377a0b9ccb474e4d7',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_8a9a2aa4d3614593959c8cade3c309d172b72f259a00a2e377a0b9ccb474e4d7'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2249988',
                                                                                 'label': 'Total Liabilities',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(Unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 9,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_8a9a2aa4d3614593959c8cade3c309d172b72f259a00a2e377a0b9ccb474e4d7',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Total '
                                                                                                     'Liabilities',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '8a9a2aa4d3614593959c8cade3c309d172b72f259a00a2e377a0b9ccb474e4d7',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_8a9a2aa4d3614593959c8cade3c309d172b72f259a00a2e377a0b9ccb474e4d7'},
                                                                                 'period_start': None,
                                                                                 'period_end': '2025-12-31',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '1715167',
                                                                                 'label': 'Total Liabilities',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026 '
                                                                                                               '(Unaudited)',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'December '
                                                                                                               '31, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 6,
                                                                                             'row': 9,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 6,
                                                                                                  'row': 3,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_3a5049737b93e898b525cf9f6073ce765dbea95e17a6f6d1bd2a54f3d53a8a94',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Royalty income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '3a5049737b93e898b525cf9f6073ce765dbea95e17a6f6d1bd2a54f3d53a8a94',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_3a5049737b93e898b525cf9f6073ce765dbea95e17a6f6d1bd2a54f3d53a8a94'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '4158812',
                                                                                 'label': 'Royalty income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_3a5049737b93e898b525cf9f6073ce765dbea95e17a6f6d1bd2a54f3d53a8a94',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Royalty income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': '3a5049737b93e898b525cf9f6073ce765dbea95e17a6f6d1bd2a54f3d53a8a94',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_3a5049737b93e898b525cf9f6073ce765dbea95e17a6f6d1bd2a54f3d53a8a94'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3089889',
                                                                                 'label': 'Royalty income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 2,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 2,
                                                                                                  'column': 6}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_c693221cdb624a49ba323234425c5cf61618e3a56bfb28d5ed416bf9409f9d82',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'c693221cdb624a49ba323234425c5cf61618e3a56bfb28d5ed416bf9409f9d82',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_c693221cdb624a49ba323234425c5cf61618e3a56bfb28d5ed416bf9409f9d82'},
                                                                                 'period_start': '2026-04-01',
                                                                                 'period_end': '2026-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '3870562',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [3]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 2,
                                                                                                  'column': 2}},
                                                                                {'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_c693221cdb624a49ba323234425c5cf61618e3a56bfb28d5ed416bf9409f9d82',
                                                                                 'native': {'issuer_cik': '0000319654',
                                                                                            'label': 'Distributable '
                                                                                                     'income',
                                                                                            'statement_scope': 'STATEMENTS '
                                                                                                               'OF '
                                                                                                               'ASSETS, '
                                                                                                               'LIABILITIES '
                                                                                                               'AND '
                                                                                                               'TRUST '
                                                                                                               'CORPUS',
                                                                                            'definition_hash': 'c693221cdb624a49ba323234425c5cf61618e3a56bfb28d5ed416bf9409f9d82',
                                                                                            'concept': '{urn:tradingml:sec-html-native:v1:cik:0000319654}N_c693221cdb624a49ba323234425c5cf61618e3a56bfb28d5ed416bf9409f9d82'},
                                                                                 'period_start': '2025-04-01',
                                                                                 'period_end': '2025-06-30',
                                                                                 'unit': 'source:$',
                                                                                 'scale': 0,
                                                                                 'decimals': '0',
                                                                                 'expected_value': '2397255',
                                                                                 'label': 'Distributable income',
                                                                                 'evidence': ['STATEMENTS OF ASSETS, '
                                                                                              'LIABILITIES AND TRUST '
                                                                                              'CORPUS'],
                                                                                 'table_proof': {'heading': 'STATEMENTS '
                                                                                                            'OF '
                                                                                                            'ASSETS, '
                                                                                                            'LIABILITIES '
                                                                                                            'AND TRUST '
                                                                                                            'CORPUS',
                                                                                                 'headers': [[1,
                                                                                                              ['',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2026',
                                                                                                               '',
                                                                                                               '',
                                                                                                               'Three '
                                                                                                               'Months '
                                                                                                               'Ended '
                                                                                                               'June '
                                                                                                               '30, '
                                                                                                               '2025',
                                                                                                               '']]]},
                                                                                 'locator': {'kind': 'cells',
                                                                                             'table': 7,
                                                                                             'row': 6,
                                                                                             'label_column': 0,
                                                                                             'columns': [7]},
                                                                                 'unit_locator': {'table': 7,
                                                                                                  'row': 2,
                                                                                                  'column': 6}}],
                                                                      'plan_hash': 'a4bde2fdb705d1c785ec4ca5ed727d1d0a35dad97cfb24b79772ed47769c0dda'}}

def plan_for(document_hash,cik):
    plan=REVIEWED.get(document_hash)
    return deepcopy(plan) if plan and plan["cik"]==str(int(cik)).zfill(10) else None
