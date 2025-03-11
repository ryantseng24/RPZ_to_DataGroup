when DNS_RESPONSE {
    # 只處理A類型記錄的回應
    if {[DNS::question type] ne "A"} {
        return
    }
    
    # 獲取所有回應記錄
    set answers [DNS::answer]
    
    # 逐條檢查回應
    foreach answer $answers {
        # 檢查記錄類型是否為A
        if {[DNS::type $answer] eq "A"} {
            # 直接提取IP地址，避免使用正則表達式
            set ip_addr [DNS::rdata $answer]
            
            # 檢查IP是否在rpz_ip資料組中
            if {[class match $ip_addr equals rpz_ip]} {
                # 設定SOA記錄
                set rpz_domain "rpz.local"
                set static_soa "$rpz_domain. 600 IN SOA localhost. IP.is.blocked.$rpz_domain. [clock seconds] 300 60 86400 60"
                
                # 清除原始回答並插入SOA
                DNS::answer clear
                DNS::answer insert $static_soa
                
                # 找到匹配後立即返回
                return
            }
        }
    }
}
