# TOPOLOGY-050 run 汇总

|run|类型|结果|轮次/工具|秒|input/cache/output/reasoning/total|
|---|---|---|---:|---:|---:|
|r_c0e31cdbcf214e6ab7af5e748440b49a|业务|failed|29/54|640.886|33116/890496/24045/null/947657|
|r_49736b405bbf4b37bd25e812555ab52d|业务|completed|14/16|325.102|4198/731136/4923/2091/740257|
|r_759fa13fe18b4f60ae871089a0fd086a|业务|interrupted|2/5|194.394|2396/116224/3723/3128/122343|
|r_105b85119d86454a9637969aaa4704bd|业务|interrupted|11/10|354.376|2300/752000/13038/10575/767338|
|r_cfaafb27474a419aa1b1d851c736f3ae|业务|completed|4/4|150.399|30214/316928/1978/987/349120|
|c_ce962c49c4504541acb464e5ec769c3a|conversation|completed|1/0|18.779|1311/0/1617/1163/2928|
|c_181c31887a2649c1bdc71137dff4d8d2|conversation|completed|1/0|20.057|3574/0/1866/1535/5440|
|c_c06409c4840c40caa218c15f361d72ac|conversation|completed|2/1|14.117|1035/12032/994/348/14061|
|c_fc0dc1ab00fd466b92cb632666988333|conversation|completed|2/1|78.090|4370/16128/2607/1985/23105|

`null` 表示 provider 没有返回该字段；第一个业务 run 的第 29 轮没有 usage。逐轮字段、工具数量和 stop/error 结果见 `topology-050-all-rounds.csv`；不含 prompt、原始参数或 secret。
