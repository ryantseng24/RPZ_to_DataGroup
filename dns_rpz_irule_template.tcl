when DNS_REQUEST {
    # set query_name
    set query_name [DNS::question name]

    # white list check
    if { [class match $query_name ends_with white_Domains] } {
        return
    }

    # black list check
    if { [class match $query_name ends_with blacklist_Domains] } {
        if { [DNS::question type] equals "A"} {
            DNS::answer insert "$query_name. 600 [DNS::question class] [DNS::question type] 34.102.218.71"
            DNS::return
        } elseif {[DNS::question type] equals "AAAA" } {
            DNS::answer insert "$query_name. 600 [DNS::question class] [DNS::question type] 2600:1901:0:9b4c::"
            DNS::return
        }
    }

    # START_DG_IP_MAP_BLOCK
set dg_ip_map {
    "phishtw_182_173_0_170" "182.173.0.170"
    "rpztw_112_121_114_76" "112.121.114.76"
    "rpztw_182_173_0_181" "182.173.0.181"
    "rpztw_210_64_24_25" "210.64.24.25"
    "rpztw_210_69_155_3" "210.69.155.3"
    "rpztw_34_102_218_71" "34.102.218.71"
    "rpztw_35_206_236_238" "35.206.236.238"
}
    # END_DG_IP_MAP_BLOCK
    foreach {dg ip} $dg_ip_map {
        if { [class match $query_name ends_with $dg] } {
            if { [DNS::question type] eq "A" } {
                DNS::answer clear
                DNS::answer insert "$query_name. 30 [DNS::question class] [DNS::question type] $ip"
                DNS::return
            } else {
                DNS::answer clear
                DNS::answer insert "$query_name. 30 IN SOA ns.rpz.local. admin.rpz.local. 2023010101 3600 600 86400 30"
                DNS::return
            }
        }
    }
}
