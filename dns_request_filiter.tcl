when DNS_REQUEST {
    # 只處理 A 類型的 DNS 查詢
    if {[DNS::question type] eq "A"} {
        # 獲取查詢名稱
        set query_name [DNS::question name]
        
        # 檢查查詢名稱是否存在於資料組中
        if {[class match $query_name equals rpz_fqdn]} {
            # 從資料組中獲取 IP 地址
            set custom_ip [class lookup $query_name rpz_fqdn]
            
            # 創建 DNS 響應
            DNS::answer clear
            DNS::answer insert "[DNS::question name]. 30 [DNS::question class] [DNS::question type] $custom_ip"
            
            # 發送響應並結束處理
            DNS::return
        }
    }
}
