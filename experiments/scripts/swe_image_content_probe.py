"""Code executed by the image's original Python; supports Python 3.6+."""
import json, subprocess, sys
base=sys.argv[1]

def tree(rev):
    records=subprocess.check_output(['git','-C','/testbed','ls-tree','-rz',rev]).split(b'\0')
    output={}
    for record in records:
        if record:
            info,path=record.split(b'\t',1)
            mode,kind,blob=info.split()
            output[path]=(kind,blob)
    return output

head=subprocess.check_output(['git','-C','/testbed','rev-parse','HEAD']).decode().strip()
before,after=tree(base),tree('HEAD')
changed=[p.decode(errors='replace') for p in set(before)|set(after) if before.get(p)!=after.get(p)]
clean=subprocess.call(['git','-C','/testbed','-c','core.filemode=false','diff','--quiet','HEAD','--','.'])==0
ancestor=subprocess.call(['git','-C','/testbed','merge-base','--is-ancestor',base,'HEAD'])==0
print(json.dumps(dict(base_is_ancestor=ancestor,source_checkout_required=bool(changed),python_version=sys.version.split()[0],image_git_head=head,base_commit=base,
    tracked_blob_identities_match=not changed,tracked_files=len(before),changed_tracked_paths=changed[:20],
    working_tree_tracked_content_clean=clean,allowance='Record official setup commits, file modes and dependency-file changes; prepare the live task from the pinned base_commit, not the image HEAD.')))
sys.exit(0 if ancestor and clean else 1)
