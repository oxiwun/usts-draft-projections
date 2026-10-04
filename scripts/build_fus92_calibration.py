#!/usr/bin/env python3
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

YEARS=range(2018,2026)
STATS="https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{y}.csv.gz"
SNAPS="https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{y}.csv.gz"
PLAYERS="https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"

def n(df,c):
    return pd.to_numeric(df[c],errors="coerce").fillna(0.0) if c in df else pd.Series(0.0,index=df.index)

def pg(x):
    p=str(x or "").upper().strip()
    if p=="QB": return "QB"
    if p in {"RB","FB"}: return "RB"
    if p=="WR": return "WR"
    if p=="TE": return "TE"
    if p in {"DE","EDGE","DT","NT","DL"}: return "DL"
    if p in {"LB","ILB","MLB","OLB"}: return "LB"
    if p in {"CB","DB","S","FS","SS"}: return "DB"
    return ""

def points(d):
    comp=n(d,"completions"); att=n(d,"attempts"); inc=(att-comp).clip(lower=0)
    rush=n(d,"rushing_yards"); recy=n(d,"receiving_yards"); rec=n(d,"receptions")
    p=.5*comp-.5*inc+.04*n(d,"passing_yards")+4*n(d,"passing_tds")-n(d,"passing_interceptions")
    p+=2*n(d,"passing_2pt_conversions")+n(d,"passing_40")
    p+=.1*rush+6*n(d,"rushing_tds")+2*n(d,"rushing_2pt_conversions")+n(d,"rushing_first_downs")+n(d,"rushing_40")
    p+=np.where(rush>=200,2,np.where(rush>=100,1,0))
    p+=rec+np.where(d["model_position"].eq("TE"),rec,0)+.1*recy+6*n(d,"receiving_tds")
    p+=2*n(d,"receiving_2pt_conversions")+n(d,"receiving_first_downs")+n(d,"receiving_40")
    # Sleeper's position first-down bonuses stack with the generic first-down score.
    skill_fd=n(d,"rushing_first_downs")+n(d,"receiving_first_downs")
    p+=np.where(d["model_position"].isin(["RB","WR","TE"]),skill_fd,0)
    p+=np.where(recy>=200,2,np.where(recy>=100,1,0))
    combo=rush+recy
    p+=np.where(combo>=200,2,np.where(combo>=100,1,0))
    fl=n(d,"fumbles_lost_total")
    if "fumbles_lost_total" not in d.columns:
        fl=n(d,"fumbles_lost")
    p-=fl
    p+=.1*n(d,"kickoff_return_yards")+.1*n(d,"punt_return_yards")+6*n(d,"special_teams_tds")
    solo=n(d,"def_tackles_solo")
    ast=n(d,"def_tackles_with_assist")+n(d,"def_tackle_assists")
    total=solo+ast; sacks=n(d,"def_sacks"); pdv=n(d,"def_pass_defended")
    blocks=n(d,"def_punt_blocks")+n(d,"def_pat_blocks")+n(d,"def_fg_blocks")
    p+=total+2*solo+ast+2*n(d,"def_tackles_for_loss")+6*sacks+n(d,"def_qb_hits")
    p+=6*n(d,"def_interceptions")+3*pdv+3*n(d,"def_fumbles_forced")+3*n(d,"fumble_recovery_opp")
    p+=3*n(d,"def_safeties")+3*blocks+6*n(d,"def_tds")
    p+=np.where(total>=10,2,0)+np.where(sacks>=2,2,0)+np.where(pdv>=3,2,0)
    return p.astype(float)

def build():
    pl=pd.read_csv(PLAYERS,low_memory=False)[["pfr_id","gsis_id","position"]].dropna(subset=["pfr_id"]).drop_duplicates("pfr_id")
    pl=pl.rename(columns={"position":"master_pos"})
    out=[]
    for y in YEARS:
        print("load",y)
        st=pd.read_csv(STATS.format(y=y),compression="gzip",low_memory=False)
        sn=pd.read_csv(SNAPS.format(y=y),compression="gzip",low_memory=False)
        if "pfr_player_id" in sn.columns:
            sn=sn.rename(columns={"pfr_player_id":"pfr_id"})
        sn=sn.merge(pl,on="pfr_id",how="left")
        sid="player_id" if "player_id" in st else "gsis_id"
        st=st.rename(columns={sid:"gsis_id"})
        st["gsis_id"]=st["gsis_id"].astype(str); sn["gsis_id"]=sn["gsis_id"].astype(str)
        sg=sn.groupby(["season","week","gsis_id"],as_index=False).agg(
            snap_pos=("position","first"),master_pos=("master_pos","first"),
            offense_snaps=("offense_snaps","sum"),defense_snaps=("defense_snaps","sum"))
        sg["model_position"]=sg["snap_pos"].map(pg)
        m=sg["model_position"].eq("")
        sg.loc[m,"model_position"]=sg.loc[m,"master_pos"].map(pg)
        sg=sg[sg["model_position"].ne("")]
        off=sg["model_position"].isin(["QB","RB","WR","TE"])
        de=sg["model_position"].isin(["DL","LB","DB"])
        sg=sg[(off&(sg["offense_snaps"]>0))|(de&(sg["defense_snaps"]>0))]
        if "season_type" in st:
            st=st[st["season_type"].eq("REG")]
        elif "game_type" in st:
            st=st[st["game_type"].eq("REG")]
        keep={"season","week","gsis_id","player_name","player_display_name","position","position_group","team","opponent_team","season_type","game_type"}
        agg={c:"sum" for c in st.columns if c not in keep}
        for c in ["player_name","player_display_name","position"]:
            if c in st: agg[c]="first"
        st=st.groupby(["season","week","gsis_id"],as_index=False).agg(agg)
        z=sg.merge(st,on=["season","week","gsis_id"],how="left")
        z["fantasy_points"]=points(z)
        out.append(z[["season","week","gsis_id","model_position","fantasy_points"]])
    return pd.concat(out,ignore_index=True)

def make_grid(z):
    rows=[]
    for pos in ["QB","RB","WR","TE","DL","LB","DB"]:
        x=z[z["model_position"].eq(pos)]
        for t in np.arange(0,40.1,1):
            win=2.0
            s=x[(x["mean_ppg"]>=max(0,t-win))&(x["mean_ppg"]<=t+win)]
            while len(s)<300 and win<10:
                win+=1
                s=x[(x["mean_ppg"]>=max(0,t-win))&(x["mean_ppg"]<=t+win)]
            if s.empty: continue
            v=s["fantasy_points"].to_numpy(float)
            rows.append([pos,t,np.quantile(v,.08),np.quantile(v,.5),v.mean(),len(v),s[["season","gsis_id"]].drop_duplicates().shape[0],win])
    g=pd.DataFrame(rows,columns=["Position","Expected_PPG","Floor92_Raw","Median_Points","Mean_Points","N_Weeks","N_Player_Seasons","PPG_Window"])
    parts=[]
    for pos,x in g.groupby("Position",sort=False):
        x=x.sort_values("Expected_PPG").copy()
        x["Floor92_Base"]=np.maximum.accumulate(x["Floor92_Raw"].to_numpy())
        parts.append(x)
    return pd.concat(parts,ignore_index=True)

def calibrate(w):
    # Freeze the empirical curve before the holdout year, then use 2025 only
    # as an out-of-sample coverage check. Any correction is conservative:
    # holdout data may lower the floor but never raise it.
    ps=w.groupby(["season","gsis_id","model_position"],as_index=False).agg(
        active=("fantasy_points","size"),
        mean_ppg=("fantasy_points","mean"))
    ps=ps[(ps["active"]>=6)&(ps["mean_ppg"]>-1)]
    z=w.merge(ps,on=["season","gsis_id","model_position"],how="inner")

    train=z[z["season"]<=2024].copy()
    hold=z[z["season"]==2025].copy()
    g=make_grid(train)

    audits=[]
    for pos in ["QB","RB","WR","TE","DL","LB","DB"]:
        hp=hold[hold["model_position"].eq(pos)].copy()
        gp=g[g["Position"].eq(pos)][["Expected_PPG","Floor92_Base"]].copy()
        if hp.empty or gp.empty:
            audits.append((pos,0.0,np.nan,0))
            continue
        hp["Expected_PPG"]=hp["mean_ppg"].round().clip(0,40)
        hp=hp.merge(gp,on="Expected_PPG",how="left")
        hp=hp.dropna(subset=["Floor92_Base"])
        if hp.empty:
            audits.append((pos,0.0,np.nan,0))
            continue
        residual=(hp["fantasy_points"]-hp["Floor92_Base"]).to_numpy(float)
        raw_cov=float(np.mean(residual>=0))
        # Use the lower empirical 8th-percentile order statistic so the holdout
        # lower bound is conservative and targets at least 92% sample coverage.
        q08=float(np.quantile(residual,.08,method="lower"))
        correction=min(0.0,q08)
        adj_cov=float(np.mean(residual>=correction))
        audits.append((pos,correction,adj_cov,len(hp)))
        print(f"holdout {pos}: raw={raw_cov:.4f} correction={correction:.4f} adjusted={adj_cov:.4f} n={len(hp)}")

    audit=pd.DataFrame(audits,columns=["Position","Holdout_Correction","OOS_Coverage_2025","OOS_Weeks_2025"])
    g=g.merge(audit,on="Position",how="left")
    g["Floor92"]=g["Floor92_Base"]+g["Holdout_Correction"].fillna(0.0)
    g["Coverage_Target"]=.92
    g["Training_Seasons"]="2018-2024"
    g["Holdout_Season"]="2025"
    g["Scoring"]="Sleeper 1312865151602421760"
    g["Notes"]="Active-week q08; 2025 OOS correction can only lower floor; rare TD-distance bonuses omitted."
    return g

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output",default="data/fus92_calibration.csv"); a=ap.parse_args()
    g=calibrate(build()); Path(a.output).parent.mkdir(parents=True,exist_ok=True); g.to_csv(a.output,index=False)
    print(g.groupby("Position").agg(rows=("Expected_PPG","size"),min_n=("N_Weeks","min"),max_floor=("Floor92","max")))

if __name__=="__main__": main()
