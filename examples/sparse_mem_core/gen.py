# Dense multi-memory core. Parameterized master/width so memory style & count scale.
NBANKS = 48; NCOLS = 8; SELW = 6
MASTER = "fakeram45_1024x32"; AW = 10; DW = 32     # addr bits, data bits
insts = []; sw = set(); bw = {}
def W(n): sw.add(n); return n
def dff(nm,d,q): insts.append(("DFF_X1",nm,[("D",d),("CK","clk"),("Q",q)]))
def g2(c,nm,a1,a2,zn): insts.append((c,nm,[("A1",a1),("A2",a2),("ZN",zn)]))
def inv(nm,a,zn): insts.append(("INV_X1",nm,[("A",a),("ZN",zn)]))
def buf(nm,a,z): insts.append(("BUF_X1",nm,[("A",a),("Z",z)]))
def tree(cell,pfx,ins):
    lvl=list(ins); k=0
    while len(lvl)>1:
        nxt=[]
        for i in range(0,len(lvl)-1,2):
            o=W(f"{pfx}_{k}"); g2(cell,f"{pfx}g{k}",lvl[i],lvl[i+1],o); nxt.append(o); k+=1
        if len(lvl)%2: nxt.append(lvl[-1])
        lvl=nxt
    return lvl[0]
bw["wq"]=DW; bw["rq"]=DW
for i in range(DW): dff(f"wreg_{i}",f"wdata[{i}]",f"wq[{i}]")
for j in range(SELW): inv(f"bsinv_{j}",f"bank_sel[{j}]",W(f"bsn_{j}"))
rmask=[[None]*DW for _ in range(NBANKS)]
for b in range(NBANKS):
    sel=tree("AND2_X1",f"seldec{b}",[f"bank_sel[{j}]" if (b>>j)&1 else f"bsn_{j}" for j in range(SELW)])
    we_b,ce_b=W(f"we_{b}"),W(f"ce_{b}")
    g2("AND2_X1",f"weg_{b}","we",sel,we_b); g2("AND2_X1",f"ceg_{b}","ce",sel,ce_b)
    bw[f"aqb{b}"]=AW; bw[f"rd{b}"]=DW; bw[f"rm{b}"]=DW
    for i in range(AW): dff(f"areg_{b}_{i}",f"addr[{i}]",f"aqb{b}[{i}]")
    insts.append((MASTER,f"bank{b}",[("clk","clk"),("ce_in",ce_b),("we_in",we_b),
        ("addr_in",f"aqb{b}"),("wd_in","wq"),
        ("w_mask_in","{"+", ".join([we_b]*DW)+"}"),("rd_out",f"rd{b}")]))
    for i in range(DW):
        g2("AND2_X1",f"rmg_{b}_{i}",f"rd{b}[{i}]",sel,f"rm{b}[{i}]"); rmask[b][i]=f"rm{b}[{i}]"
for i in range(DW):
    rc=tree("OR2_X1",f"rtree{i}",[rmask[b][i] for b in range(NBANKS)])
    buf(f"rcbuf_{i}",rc,f"rdata_c[{i}]")
    dff(f"rreg_{i}",rc,f"rq[{i}]"); buf(f"rrbuf_{i}",f"rq[{i}]",f"rdata_r[{i}]")
bp={"addr":AW,"wdata":DW,"bank_sel":SELW,"rdata_c":DW,"rdata_r":DW}
L=["module sparse_mem_core(clk, ce, we, addr, wdata, bank_sel, rdata_c, rdata_r);"]
for s in ("clk","ce","we"): L.append(f"  input {s};")
for nm,w in bp.items(): L.append(f"  {'output' if nm.startswith('rdata') else 'input'} [{w-1}:0] {nm};")
for nm,w in bw.items(): L.append(f"  wire [{w-1}:0] {nm};")
for n in sorted(sw): L.append(f"  wire {n};")
for ct,nm,cn in insts: L.append(f"  {ct} {nm} ( "+", ".join(f".{p}({e})" for p,e in cn)+" );")
L.append("endmodule")
import os as _o
open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)),"sparse_mem_core.v"),"w").write("\n".join(L)+"\n")
print("master",MASTER,"| banks",NBANKS,"| std cells",sum(1 for c,_,_ in insts if c!=MASTER),
      "| ports",3+sum(bp.values()))
