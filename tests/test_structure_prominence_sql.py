"""Compare every SQL prefix against the actual shared Rust streaming scorer."""
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import unittest


def module(name, file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/file)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P=module('prototype','prototype_structure_book_clickhouse.py')
S=module('score_sql','structure_prominence_sql.py')


@unittest.skipUnless(os.environ.get('STRUCTURE_PROTOTYPE_SQL_TEST')=='1','SQL fixture integration opt-in')
class ScoreSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=P.Client(Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'),4)
        cls.oracle=Path(os.environ.get('STRUCTURE_SCORE_ORACLE',r'D:\TradingML\runtimes\qmd_history_gateway\consumer-v18-target\debug\structure_score_fixture.exe'))
        if not cls.oracle.is_file():
            raise RuntimeError('Build structure_score_fixture before SQL equivalence tests')

    def compare(self, rows):
        expected=json.loads(subprocess.run([str(self.oracle)],input=json.dumps(rows)+'\n',
            text=True,capture_output=True,check=True,timeout=30).stdout)
        def row_sql(row):
            return 'tuple('+','.join(format(float(n),'.17g')+'.' if float(n).is_integer() else repr(float(n)) for n in row)+')'
        values='['+','.join(map(row_sql,rows))+']'
        fold=S.fold_sql('arraySlice(events,1,n)')
        query=f'WITH {values} AS events SELECT arrayMap(n->{fold},range(1,length(events)+1)) AS prefixes'
        actual=self.client.query(query,'score_prefixes')[0]['prefixes']
        self.assertEqual(len(actual),len(expected))
        for index,(a,e) in enumerate(zip(actual,expected)):
            for got,want in zip(a,e):
                self.assertAlmostEqual(got,want,delta=1e-10*max(1,abs(want)),msg=f'prefix {index}: {a} != {e}')
        split_at=len(rows)//2
        seed=S.fold_sql(f'arraySlice(events,1,{split_at})')
        resumed=S.fold_sql(f'arraySlice(events,{split_at+1})','seed')
        query=f'WITH {values} AS events,{seed} AS seed SELECT {resumed} AS state'
        state=self.client.query(query,'score_resume')[0]['state']
        for got,want in zip(state,expected[-1]):
            self.assertAlmostEqual(got,want,delta=1e-10*max(1,abs(want)))

    def test_contact_departure_return_break_role_flip_split(self):
        rows=[[p,9.9,10.1,1,sigma,1,0,1] for p,sigma in [(10,0),(10,1),(10.3,9),(10,9),(12.1,9),(10,2),(13.1,2)]]
        rows += [[13.1*75,9.9*75,10.1*75,1,150,1,0,75],
                 [9*75,9.9*75,10.1*75,1,150,0,1,1],
                 [10*75,9.9*75,10.1*75,-1,150,1,0,1],
                 [7*75,9.9*75,10.1*75,-1,150,1,0,1]]
        self.compare(rows)

    def test_randomized_causal_prefixes(self):
        rng=random.Random(23)
        for case in range(8):
            rows=[]
            for i in range(40):
                side=1 if i<20 else -1
                rows.append([rng.choice([9,9.95,10,10.05,10.5,11,12]),9.9,10.1,side,
                             rng.choice([0,.1,.5,1]),int(i%13!=0),int(i%17==0),1])
            self.compare(rows)


if __name__=='__main__': unittest.main()
