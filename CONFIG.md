# 配置参考 (CONFIG.md)

本文件由 `tools/gen_config_doc.py` 自动生成，列出全部配置项、默认值与说明。
配置单一事实来源为 SQLite `app_config` 表（Web 配置中心实时读写）。

## 环境变量覆盖（对标 iptv-api）

任何配置键均可被环境变量 `LSM_<SECTION>_<KEY>` 覆盖，优先级：**环境变量 > SQLite(用户/默认) > 代码默认**。
其中 `<SECTION>` 与 `<KEY>` 为配置段名与键名大写，点号 `.` 替换为下划线 `_`。

示例：

```bash
# 使用 aiohttp 异步测速（而非默认 ffprobe）
export LSM_TESTING_TEST_METHOD=aiohttp

# 关闭 IPv4/IPv6 分文件发布
export LSM_OUTPUT_SEPARATE_IPV4_IPV6=False

# 关闭候选池择优闭环
export LSM_OUTPUT_CANDIDATE_POOL_ENABLED=False
```

> 注意：环境变量覆盖在进程启动后持续生效，修改需重启服务。

## [Sources]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| local_dirs | 字符串 | ./config/sources | 本地源目录；逗号分隔 | `LSM_SOURCES_LOCAL_DIRS` |
| online_urls | 多行文本 | https://live.zbds.org/tv/iptv4.m3u https://myernestlu.github.io/zby.txt https://raw.githubusercontent.com/Rivens7/Livelist/main/CCTV.m3u https://raw.githubusercontent.com/Rivens7/Livelist/main/CNTV.m3u https://raw.githubusercontent.com/Rivens7/Livelist/main/IPTV.m3u https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/result.m3u https://raw.githubusercontent.com/suxuang/myIPTV/refs/heads/main/ipv4.m3u https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8 https://raw.githubusercontent.com/zwc456baby/iptv_alive/refs/heads/master/live.m3u https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u https://raw.githubusercontent.com/vamoschuck/TV/main/M3U https://raw.githubusercontent.com/BigBigGrandG/IPTV-URL/release/Gather.m3u https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u https://raw.githubusercontent.com/huang770101/my-iptv/main/IPTV-ipv4.m3u https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u https://live.fanmingming.cn/tv/m3u/ipv6.m3u https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u https://raw.githubusercontent.com/iptv-org/iptv/master/streams/tw.m3u https://raw.githubusercontent.com/iptv-org/iptv/master/streams/hk.m3u | 在线源URL列表；每行一个URL | `LSM_SOURCES_ONLINE_URLS` |
| github_sources | 多行文本 | joevess/IPTV/main suxuang/myIPTV/main YueChan/Live YanG-1989/m3u qwerttvv/Beijing-IPTV joevess/IPTV cymz6/AutoIPTV-Hotel Rivens7/Livelist | GitHub仓库；格式: owner/repo | `LSM_SOURCES_GITHUB_SOURCES` |
| github_source_settings | 多行文本 | {} | GitHub源下载方式；JSON：{条目: 方式}，方式=raw(默认)/api/proxy/mirror。海外/区域受限仓库设 "proxy" 经代理拉取 | `LSM_SOURCES_GITHUB_SOURCE_SETTINGS` |
| per_source_ua | 多行文本 | {} | 每源UA；JSON：{源URL: User-Agent}，在线/本地源均生效（频道级覆盖，优先级最高） | `LSM_SOURCES_PER_SOURCE_UA` |
| auto_disable_enabled | 布尔 | True | 失效自动停用；连续失败达到阈值的源自动停用，不再参与测试/发布，冷却后自动恢复重试 | `LSM_SOURCES_AUTO_DISABLE_ENABLED` |
| auto_disable_fail_threshold | 整数 | 5 | 停用阈值；同一源连续失败达到该次数后自动停用 | `LSM_SOURCES_AUTO_DISABLE_FAIL_THRESHOLD` |
| auto_disable_cooldown_hours | 整数 | 24 | 恢复冷却(小时)；停用源经过该小时数后自动恢复并重试，防止永久误杀 | `LSM_SOURCES_AUTO_DISABLE_COOLDOWN_HOURS` |

## [Network]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| proxy_enabled | 布尔 | False | 启用代理；True/False | `LSM_NETWORK_PROXY_ENABLED` |
| proxy_type | 字符串 | socks5 | 代理类型；http/https/socks5 | `LSM_NETWORK_PROXY_TYPE` |
| proxy_host | 字符串 | 192.168.1.46 | 代理主机； | `LSM_NETWORK_PROXY_HOST` |
| proxy_port | 整数 | 1800 | 代理端口； | `LSM_NETWORK_PROXY_PORT` |
| proxy_username | 字符串 |  | 代理用户名； | `LSM_NETWORK_PROXY_USERNAME` |
| proxy_password | 字符串 |  | 代理密码； | `LSM_NETWORK_PROXY_PASSWORD` |
| github_mirror | 字符串 | https://ghproxy.com/ | GitHub镜像站；用于 mirror 下载方式的代理网站URL | `LSM_NETWORK_GITHUB_MIRROR` |
| ipv6_enabled | 布尔 | True | 启用IPv6； | `LSM_NETWORK_IPV6_ENABLED` |

## [HTTPServer]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| enabled | 布尔 | True | 启用HTTP； | `LSM_HTTPSERVER_ENABLED` |
| host | 字符串 | 0.0.0.0 | 监听地址； | `LSM_HTTPSERVER_HOST` |
| fileshare_port | 整数 | 12345 | 文件共享端口； | `LSM_HTTPSERVER_FILESHARE_PORT` |
| manager_port | 整数 | 23456 | 管理端口； | `LSM_HTTPSERVER_MANAGER_PORT` |
| document_root | 字符串 | ./www/output | 文档根目录； | `LSM_HTTPSERVER_DOCUMENT_ROOT` |

## [GitHub]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| api_url | 字符串 | https://api.github.com | API地址；GitHub API 基地址，一般无需修改 | `LSM_GITHUB_API_URL` |
| api_token | 字符串 |  | API Token；GitHub Personal Access Token（无需任何权限，仅用于提升 API 速率限制至 5000次/时）。前往 GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token 生成 | `LSM_GITHUB_API_TOKEN` |
| rate_limit | 整数 | 5000 | 速率限制；每小时最大 API 请求次数（有 Token: 5000，无 Token: 60） | `LSM_GITHUB_RATE_LIMIT` |

## [Testing]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| timeout | 整数 | 10 | 测试超时(秒)； | `LSM_TESTING_TIMEOUT` |
| concurrent_threads | 整数 | 50 | 并发线程数； | `LSM_TESTING_CONCURRENT_THREADS` |
| max_concurrent_ffprobe | 整数 | 16 | ffprobe并发数(实时测试)； | `LSM_TESTING_MAX_CONCURRENT_FFPROBE` |
| cache_ttl | 整数 | 120 | 缓存有效期(分)； | `LSM_TESTING_CACHE_TTL` |
| enable_speed_test | 布尔 | True | 启用速率测试； | `LSM_TESTING_ENABLE_SPEED_TEST` |
| speed_test_duration | 整数 | 3 | 速率测试时长(秒)； | `LSM_TESTING_SPEED_TEST_DURATION` |
| auto_scan_enabled | 布尔 | False | 启用自动扫描测试； | `LSM_TESTING_AUTO_SCAN_ENABLED` |
| auto_scan_mode | 字符串 | interval | 自动扫描模式；interval=按间隔小时数；daily=每日指定时刻（与下方参数配合） | `LSM_TESTING_AUTO_SCAN_MODE` |
| auto_scan_interval_hours | 整数 | 24 | 间隔小时数；mode=interval 时生效：每 N 小时自动测试一次 | `LSM_TESTING_AUTO_SCAN_INTERVAL_HOURS` |
| auto_scan_daily_time | 字符串 | 03:00 | 每日启动时刻；mode=daily 时生效：格式 HH:MM，每天该时刻自动测试一次 | `LSM_TESTING_AUTO_SCAN_DAILY_TIME` |
| enable_host_speed_share | 布尔 | True | 同 Host 测速复用；同 CDN/Host 仅 ffprobe 一次并复用结果，大幅减少重复探测（对标 Guovin） | `LSM_TESTING_ENABLE_HOST_SPEED_SHARE` |
| enable_source_freeze | 布尔 | True | 失败源冻结；连续失败的源按 2^n×基数 秒指数退避冻结冷却，省资源 | `LSM_TESTING_ENABLE_SOURCE_FREEZE` |
| freeze_fail_threshold | 整数 | 3 | 冻结阈值；连续失败达到该次数后开始冻结 | `LSM_TESTING_FREEZE_FAIL_THRESHOLD` |
| freeze_base_seconds | 整数 | 60 | 退避基数(秒)；冻结时长 = 2^失败次数 × 基数，封顶 freeze_max_hours | `LSM_TESTING_FREEZE_BASE_SECONDS` |
| freeze_max_hours | 整数 | 24 | 冻结上限(小时)；单次冻结最长时间 | `LSM_TESTING_FREEZE_MAX_HOURS` |
| enable_ad_detect | 布尔 | True | 广告/循环源检测；拉取 m3u8 检查广告关键字与循环占位标志 | `LSM_TESTING_ENABLE_AD_DETECT` |
| ad_keywords | 字符串 | no_signal,/ad/,advertisement,测试卡,无信号,test_pattern,colorbar,broadcast_test,signal_lost | 广告关键字；命中即判为广告源（逗号或换行分隔） | `LSM_TESTING_AD_KEYWORDS` |
| ad_max_duration | 整数 | 90 | 循环占位阈值(秒)；含 #EXT-X-ENDLIST 且累计时长<=该值判为循环占位 | `LSM_TESTING_AD_MAX_DURATION` |
| global_blacklist | 字符串 |  | 全局黑名单；命中(URL/host)的源跳过测试，逗号或换行分隔 | `LSM_TESTING_GLOBAL_BLACKLIST` |
| global_whitelist | 字符串 |  | 全局白名单；URL/host 清单，豁免于黑名单与冻结，逗号或换行分隔 | `LSM_TESTING_GLOBAL_WHITELIST` |
| output_sort_by | 字符串 | speed | 输出排序；speed=快源在前；name=按名；resolution=按分辨率 | `LSM_TESTING_OUTPUT_SORT_BY` |
| max_test_attempts | 整数 | 1 | 实时测试次数；每个地址的总测试次数：1=测一次；2=测两次(含1次自动重试)；默认1 | `LSM_TESTING_MAX_TEST_ATTEMPTS` |
| test_method | 字符串 | ffprobe | 测速引擎；ffprobe=默认，使用 ffprobe/ffmpeg 探测完整元数据(分辨率/编码/比特率)；aiohttp=异步下载分片算速+延迟，轻量但无分辨率元数据 | `LSM_TESTING_TEST_METHOD` |

## [Output]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| filename | 字符串 | live.m3u | 输出文件名； | `LSM_OUTPUT_FILENAME` |
| group_by | 字符串 | category | 分组策略； | `LSM_OUTPUT_GROUP_BY` |
| include_failed | 布尔 | False | 包含失败源； | `LSM_OUTPUT_INCLUDE_FAILED` |
| max_sources_per_channel | 整数 | 5 | 每频道最大源数； | `LSM_OUTPUT_MAX_SOURCES_PER_CHANNEL` |
| output_all_valid | 布尔 | False | 输出全部有效源；开启后 live.m3u 直接用全部有效源(跳过分辨率聚合与质量过滤)，保留所有检测通过的频道(含收音机/港台/MTV/电影) | `LSM_OUTPUT_OUTPUT_ALL_VALID` |
| enable_filter | 布尔 | True | 启用分层过滤；关闭后 base/qualified 均直接用全量有效源，等效关闭分辨率聚合与质量过滤 | `LSM_OUTPUT_ENABLE_FILTER` |
| whitelist_force_keep | 布尔 | False | 白名单强制保留；白名单源即使未过质量过滤也保留到输出 | `LSM_OUTPUT_WHITELIST_FORCE_KEEP` |
| candidate_pool_enabled | 布尔 | True | 候选池择优；启用后每次测速留存全部结果到候选池，输出时按指标选 Top N 并固定手动冻结的优选源（对标 iptv-api） | `LSM_OUTPUT_CANDIDATE_POOL_ENABLED` |
| auto_select_metric | 字符串 | speed | 择优指标；speed=快源优先；latency=延迟低优先；resolution=分辨率高优先 | `LSM_OUTPUT_AUTO_SELECT_METRIC` |
| separate_ipv4_ipv6 | 布尔 | True | IPv4/IPv6 分文件；在 live.m3u(双栈共存) 之外，额外生成 live-ipv4.m3u 与 live-ipv6.m3u 单栈文件 | `LSM_OUTPUT_SEPARATE_IPV4_IPV6` |
| ipv4_filename | 字符串 | live-ipv4.m3u | IPv4 文件名； | `LSM_OUTPUT_IPV4_FILENAME` |
| ipv6_filename | 字符串 | live-ipv6.m3u | IPv6 文件名； | `LSM_OUTPUT_IPV6_FILENAME` |

## [Logging]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| level | 字符串 | INFO | 日志级别； | `LSM_LOGGING_LEVEL` |
| file | 字符串 | ./log/app.log | 日志文件路径； | `LSM_LOGGING_FILE` |
| max_size | 整数 | 10 | 最大日志大小(MB)； | `LSM_LOGGING_MAX_SIZE` |
| backup_count | 整数 | 5 | 备份文件数； | `LSM_LOGGING_BACKUP_COUNT` |

## [Filter]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| max_latency | 整数 | 4000 | 最大延迟(ms)； | `LSM_FILTER_MAX_LATENCY` |
| min_bitrate | 整数 | 80 | 最小比特率(kbps)； | `LSM_FILTER_MIN_BITRATE` |
| must_hd | 布尔 | False | 必须高清； | `LSM_FILTER_MUST_HD` |
| must_4k | 布尔 | False | 必须4K； | `LSM_FILTER_MUST_4K` |
| min_speed | 整数 | 50 | 最小下载速度(KB/s)； | `LSM_FILTER_MIN_SPEED` |
| min_resolution | 字符串 | 360p | 最低分辨率； | `LSM_FILTER_MIN_RESOLUTION` |
| max_resolution | 字符串 | 4k | 最高分辨率； | `LSM_FILTER_MAX_RESOLUTION` |
| resolution_filter_mode | 字符串 | range | 分辨率筛选模式； | `LSM_FILTER_RESOLUTION_FILTER_MODE` |

## [UserAgents]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| ua_position | 字符串 | extinf | UA位置； | `LSM_USERAGENTS_UA_POSITION` |
| ua_enabled | 布尔 | False | 启用UA； | `LSM_USERAGENTS_UA_ENABLED` |

## [EPG]

| 键 | 类型 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- | --- |
| enabled | 布尔 | True | 启用EPG；开启后按调度抓取节目单并生成 XMLTV | `LSM_EPG_ENABLED` |
| output_filename | 字符串 | epg.xml.gz | EPG输出文件名；生成到输出目录，供播放器拉取 | `LSM_EPG_OUTPUT_FILENAME` |
| refresh_mode | 字符串 | daily | 刷新方式；daily=每天定点；interval=按间隔分钟 | `LSM_EPG_REFRESH_MODE` |
| refresh_at | 字符串 | 03:30 | 每日刷新时刻；refresh_mode=daily 时生效，格式 HH:MM | `LSM_EPG_REFRESH_AT` |
| refresh_minutes | 整数 | 360 | 刷新间隔(分钟)；refresh_mode=interval 时生效，最小5 | `LSM_EPG_REFRESH_MINUTES` |
| timezone | 字符串 | Asia/Shanghai | 时区；节目单展示时区 | `LSM_EPG_TIMEZONE` |
| keep_days | 整数 | 7 | 保留天数；仅保留未来 N 天的节目 | `LSM_EPG_KEEP_DAYS` |
| past_hours | 整数 | 6 | 保留过去小时数；保留过去 N 小时节目，供"正在播"回看 | `LSM_EPG_PAST_HOURS` |
| fetch_timeout | 整数 | 60 | 抓取超时(秒)；单个 EPG 源下载超时 | `LSM_EPG_FETCH_TIMEOUT` |
| web_base_url | 字符串 |  | EPG外链基址；留空自动探测 http://<host>:<发布端口> | `LSM_EPG_WEB_BASE_URL` |
| inject_into_m3u | 布尔 | True | 注入M3U头；在 #EXTM3U 注入 url-tvg 指向 EPG 文件 | `LSM_EPG_INJECT_INTO_M3U` |

## 其他内部默认键（未暴露在配置中心 UI）

| 键 | 默认值 | 环境变量 |
| --- | --- | --- |
| Sources.source_file_ua_settings | {} | `LSM_SOURCES_SOURCE_FILE_UA_SETTINGS` |
| Sources.channel_ua_overrides | {} | `LSM_SOURCES_CHANNEL_UA_OVERRIDES` |
| Network.download_connect_timeout | 10 | `LSM_NETWORK_DOWNLOAD_CONNECT_TIMEOUT` |
| Network.download_total_timeout | 30 | `LSM_NETWORK_DOWNLOAD_TOTAL_TIMEOUT` |
| Network.download_batch_size | 12 | `LSM_NETWORK_DOWNLOAD_BATCH_SIZE` |
