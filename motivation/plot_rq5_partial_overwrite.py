#!/usr/bin/env python3
"""Plot the supplemental non-truncating edit measurements in the RQ5 style."""
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from motivation.plot_motivation_four import CM, configure_style
from motivation.plot_rq5_storage import RQ5_PALETTE, VARIANTS

SOURCE = ROOT/'review/rq5-partial-overwrite-20261117/summary.json'
OUTPUT = ROOT/'paper/figures/rq5-partial-overwrite'
FILESYSTEMS = ('ext4','xfs-noreflink','xfs-reflink')
TITLES = ('Ext4','XFS (reflink off)','XFS (reflink on)')


def main():
    configure_style()
    summary = json.loads(SOURCE.read_text())
    assert summary['counts']['measured']==2700 and summary['counts']['failed']==0
    fig,axes = plt.subplots(2,3,figsize=(17.8*CM,9.2*CM))
    fig.subplots_adjust(left=.08,right=.99,top=.865,bottom=.16,wspace=.48,hspace=.85)
    handles, evidence = {}, {}
    sizes = np.array([4,8,16,32,64,128,256,512,1024])
    styles = (('v','-.'),('o','--'),('^',':'),('s','-'))
    for col,(fs,title) in enumerate(zip(FILESYSTEMS,TITLES)):
        for row,(metric,ylabel,caption) in enumerate((
                ('edit_write_kib','Write I/O (KiB/edit)','Block-device writes'),
                ('edit_synced_ms','Time (ms/edit)','Edit including syncfs'))):
            ax = axes[row,col]
            letter = chr(ord('a')+row*3+col)
            evidence[letter] = dict(filesystem=fs,metric=metric,groups={})
            for index,(variant,(marker,line)) in enumerate(zip(VARIANTS,styles)):
                cells = [summary['io'][fs][variant][str(size)][metric] for size in sizes]
                assert all(cell['n']==20 for cell in cells)
                means,lows,highs = [np.array([cell[key] if key=='mean' else cell['ci95'][key] for cell in cells])
                                    for key in ('mean',0,1)]
                color = RQ5_PALETTE[VARIANTS[variant][1]]
                ax.fill_between(sizes,lows,highs,color=color,alpha=.12,linewidth=0)
                artist, = ax.plot(sizes,means,color=color,linewidth=.9,linestyle=line,
                                  marker=marker,markersize=3,markeredgewidth=.4,
                                  markevery=(index%3,3),zorder=3)
                handles[variant] = artist
                evidence[letter]['groups'][variant] = {
                    str(size):dict(mean=float(m),ci95=[float(lo),float(hi)],n=cell['n'])
                    for size,m,lo,hi,cell in zip(sizes,means,lows,highs,cells)}
            ax.set_xscale('log',base=2)
            ax.set_xticks([4,16,64,256,1024],['4','16','64','256','1024'])
            ax.set_xlim(3.6,1150)
            if row==0:
                ax.set_yscale('log',base=2)
                ax.set_ylim(3,1600)
                ax.set_yticks([4,16,64,256,1024],['4','16','64','256','1024'])
                ax.set_title(title,fontsize=8,fontweight='bold',pad=4)
            else:
                ax.set_ylim(0,6)
                ax.set_yticks([0,2,4,6])
            ax.set_ylabel(ylabel,fontsize=8,labelpad=3)
            ax.set_xlabel(f'File size (KiB)\n({letter}) {caption}',fontsize=8,labelpad=5)
            ax.tick_params(direction='in',labelsize=7.3,length=2,pad=2)
            ax.grid(axis='y',color='#e5e5e5',linewidth=.35,zorder=0)
            ax.set_axisbelow(True)
    fig.legend([handles[v] for v in VARIANTS],
               ['Native FS' if v=='native' else VARIANTS[v][0] for v in VARIANTS],
               loc='upper center',bbox_to_anchor=(.53,.998),ncol=4,fontsize=7.7,
               handlelength=1.8,handletextpad=.45,columnspacing=1.3,borderaxespad=0)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    for suffix in ('pdf','png'):
        fig.savefig(OUTPUT/f'FIG-RQ5-Partial-Overwrite.{suffix}',dpi=300,bbox_inches='tight',pad_inches=.025)
    (OUTPUT/'figure-data.json').write_text(json.dumps(dict(source=str(SOURCE.relative_to(ROOT)),
        sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),panels=evidence,
        sampling='20 sessions per point, average 5 edits within each session; 95% session-bootstrap intervals.',
        counts=summary['counts']),indent=2)+'\n')
    plt.close(fig)
    print('Generated six supplemental panels: block-device writes and synced edit latency by backing FS.')


if __name__=='__main__':
    main()
