import os, time
from pythonosc.udp_client import SimpleUDPClient

host = os.environ.get("RITSU_VMC_HOST","127.0.0.1")
port = int(os.environ.get("RITSU_VMC_PORT","39539"))
c = SimpleUDPClient(host, port)

def apply(name, v=1.0, hold=0.9):
    c.send_message("/VMC/Ext/Blend/Val", [name, float(v)])
    c.send_message("/VMC/Ext/Blend/Apply", [])
    time.sleep(hold)
    c.send_message("/VMC/Ext/Blend/Val", [name, 0.0])
    c.send_message("/VMC/Ext/Blend/Apply", [])
    time.sleep(0.25)

seq = ["Joy","Fun","Surprised","Angry","Sorrow","Neutral",
       "happy","relaxed","surprised","angry","sad",
       "Blink","blink"]
print("HOST",host,"PORT",port)
for n in seq:
    print("send",n)
    apply(n)
print("DONE")
