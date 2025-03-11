when DNS_RESPONSE {
    # 設定RPZ域名和SOA記錄
    set rpz_domain "rpz.local"
    set static_soa "$rpz_domain. 600 IN SOA localhost. IP.is.blocked.$rpz_domain. [clock seconds] 300 60 86400 60"
    
    # 檢查是否需要封鎖
    set block_response 0
    
    # 獲取原始DNS問題
    set qname [DNS::question name]
    set qtype [DNS::question type]
    
    # 只處理A類型記錄的回應
    if {$qtype eq "A"} {
        # 檢查所有回應記錄
        set answers [DNS::answer]
        foreach answer $answers {
            # 解析回應中的IP地址
            if {[regexp {^(\S+)\s+\d+\s+IN\s+A\s+(\S+)$} $answer -> record_name ip_addr]} {
                # 檢查IP是否在rpz_ip資料組中
                if {[class match $ip_addr equals rpz_ip]} {
                    set block_response 1
                    break
                }
            }
        }
    }
    
    # 如果需要封鎖響應，替換為SOA記錄
    if {$block_response} {
        # 清除所有原始回答
        DNS::answer clear
        
        # 插入SOA記錄
        DNS::answer insert $static_soa
        
        # 設置DNS響應碼為NXDOMAIN（可選）
        # DNS::header rcode NXDOMAIN
    }
}
