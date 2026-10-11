"""Read-only inventory of named immutable research snapshots."""
import json, os, urllib.parse
from research_backend_client import Client, BackendError

NAMES = [
 "portfolio-whole-wealth-baseline-50000-20261011-v3",
 "portfolio-whole-wealth-baseline-50000-risk-review-20261011-v1",
 "portfolio-whole-wealth-inputs-20261011-v1",
]
class ReadOnlyClient(Client):
 def request(self, method, path, body=None, file=None):
  if method != "GET": raise ValueError("Read-only audit")
  return super().request(method,path,body,file)
def main():
 client=ReadOnlyClient(project="investment")
 for name in NAMES:
  try:
   head=client.json("GET","/snapshot-heads/"+urllib.parse.quote(name,safe=""))
   sid=head.get("snapshot_id")
   members=list(client.snapshot_entries(sid)) if sid else []
   print("SNAPSHOT_INVENTORY "+json.dumps({"name":name,"head":head,"members":members},ensure_ascii=False))
  except BackendError as e:
   print(json.dumps({"name":name,"http_status":e.status}))
 for kind in ["experiments","checkpoints"]:
  data=client.json("GET",f"/records/{kind}?limit=20")
  items=data.get("items",[])
  safe=[{k:v for k,v in item.items() if k in ["key","summary","version"]} for item in items]
  print("RECORD_INDEX "+json.dumps({"kind":kind,"keys":list(data),"items":safe,"next_cursor":data.get("next_cursor")},ensure_ascii=False))
if __name__=="__main__":
 try: main()
 except Exception as e:
  print(json.dumps({"error_type":type(e).__name__,"http_status":getattr(e,"status",None)})); raise SystemExit(1)
