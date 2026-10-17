"""Compact pair diagnostics; full numeric matrices live in compressed NPZ."""
import html,json
import numpy as np


def summarize(matrices,candidates):
    j=matrices['jaccard'];c=matrices['cosine'];coverage=matrices['coverage'];n=len(candidates)
    i,k=np.triu_indices(n,1);valid=np.isfinite(j[i,k]);i,k=i[valid],k[valid]
    def pair(a,b):return dict(left=int(candidates[a]['serial']),right=int(candidates[b]['serial']),
        jaccard=float(j[a,b]),cosine=float(c[a,b]),coverage_left=float(coverage[a,b]),coverage_right=float(coverage[b,a]))
    top=np.argsort(-j[i,k],kind='stable')[:50];low=np.argsort(j[i,k],kind='stable')[:50]
    neighbors=[]
    for a in range(n):
        options=np.flatnonzero(np.isfinite(j[a])&(np.arange(n)!=a));order=options[np.argsort(-j[a,options],kind='stable')[:5]]
        neighbors.append(dict(serial=int(candidates[a]['serial']),neighbors=[pair(a,int(b)) for b in order]))
    subsets=[]
    for a,b in zip(i,k):
        if coverage[a,b]>=.95 and coverage[b,a]<=.8:subsets.append(pair(a,b))
        elif coverage[b,a]>=.95 and coverage[a,b]<=.8:subsets.append(pair(b,a))
    subsets.sort(key=lambda x:(-x['coverage_left'],x['coverage_right'],x['left'],x['right']))
    return dict(defined_pairs=len(i),undefined_pairs=n*(n-1)//2-len(i),
        almost_identical_pairs=int(np.sum(j[i,k]>=.95)),most_similar=[pair(int(i[x]),int(k[x])) for x in top],
        least_similar=[pair(int(i[x]),int(k[x])) for x in low],subsets=subsets[:50],nearest_neighbors=neighbors)


def render(result,path):
    def table(headers,rows):return '<table><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'
    body=['<h1>Final V26 occupancy overlap</h1>',
          '<p>Post-Phase-2 analysis only. Candidates were frozen by training selection before validation; the optimizer is unchanged. Occupancy uses [entry, exit) on the one-second replay clock, with a one-second minimum for zero-length records, matching the existing ledger convention. Quantity and intermediate adds/reductions are not weights. Empty exposure is undefined.</p>',
          '<p>Jaccard = shared seconds / union seconds. Cosine = shared seconds / sqrt(seconds A × seconds B). Coverage A = shared seconds / seconds A. Sessions/listings never cross-match. Exact sparse interval integrals avoid a dense binary grid.</p>']
    body.append(table(['Serial','Training rank','Training profit','Validation profit'],[(c['serial'],c['training_rank'],f"${c['training_profit']:,.2f}",f"${c['validation_profit']:,.2f}") for c in result['candidates']]))
    for role,split in result['splits'].items():
        body.append('<h2>'+role.title()+'</h2><p>'+str(split['summary']['defined_pairs'])+' defined candidate pairs; '+str(split['summary']['almost_identical_pairs'])+' pairs have Jaccard ≥95%.</p>')
        for key,title in [('most_similar','Most similar'),('least_similar','Least similar'),('subsets','Mostly contained exposures')]:
            body.append('<h3>'+title+'</h3>'+table(['A','B','Jaccard','Cosine','Coverage A','Coverage B'],[(p['left'],p['right'],*[f'{p[k]:.2%}' for k in ['jaccard','cosine','coverage_left','coverage_right']]) for p in split['summary'][key]]))
    body.append('<p>Full matrices are stored in training.npz and validation.npz; row order matches the candidate table. Per-candidate neighbors and all provenance are in result.json.</p>')
    path.write_text('<!doctype html><meta charset="utf-8"><title>V26 occupancy</title><style>body{font:15px system-ui;margin:30px;color:#17334a}table{border-collapse:collapse;font-size:13px}th,td{padding:7px 14px;border-bottom:1px solid #ddd;text-align:right}p{max-width:1100px;line-height:1.6}</style>'+''.join(body),encoding='utf-8')
