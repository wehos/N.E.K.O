/**
 * i18next 初始化文件
 * 使用成熟的 i18next 库替代自定义 i18n 方案
 * 
 * 使用方式：
 * 1. 在 HTML 中引入 i18next CDN：
 *    <script src="https://cdn.jsdelivr.net/npm/i18next@23.7.6/dist/umd/i18next.min.js"></script>
 *    <script src="https://cdn.jsdelivr.net/npm/i18next-browser-languagedetector@7.2.0/dist/umd/i18nextBrowserLanguageDetector.min.js"></script>
 *    <script src="https://cdn.jsdelivr.net/npm/i18next-http-backend@2.4.2/dist/umd/i18nextHttpBackend.min.js"></script>
 * 2. 然后引入此文件：
 *    <script src="/static/i18n-i18next.js"></script>
 */

(function() {
    'use strict';
    
    // 先定义诊断函数（即使 i18next 未加载也能使用）
    window.diagnoseI18n = function() {
        console.log('=== i18next 诊断信息 ===');
        console.log('1. i18next 是否存在:', typeof i18next !== 'undefined');
        console.log('2. window.setLocale 是否存在:', typeof window.setLocale === 'function');
        console.log('3. window.t 是否存在:', typeof window.t === 'function');
        console.log('4. window.i18n 是否存在:', typeof window.i18n !== 'undefined');
        
        if (typeof i18next !== 'undefined') {
            console.log('5. i18next.isInitialized:', i18next.isInitialized);
            console.log('6. 当前语言:', i18next.language);
            console.log('7. 支持的语言:', i18next.options?.supportedLngs);
            console.log('8. 已加载的资源:', Object.keys(i18next.store?.data || {}));
        } else {
            console.error('5. i18next 未加载！请检查 CDN 是否成功加载。');
        }
        
        console.log('9. localStorage preferredLocale:', localStorage.getItem('preferredLocale'));
        console.log('10. 浏览器语言:', navigator.language);
        
        // 检查页面上的 data-i18n 元素
        const elements = document.querySelectorAll('[data-i18n]');
        console.log(`11. 页面上的 data-i18n 元素数量: ${elements.length}`);
        if (elements.length > 0) {
            console.log('12. 前3个元素:');
            Array.from(elements).slice(0, 3).forEach((el, i) => {
                const key = el.getAttribute('data-i18n');
                const text = el.textContent;
                console.log(`   元素 ${i+1}: key="${key}", text="${text}"`);
            });
        }
        
        console.log('=== 诊断完成 ===');
    };
    
    // 测试翻译函数
    window.testTranslation = function(key) {
        console.log(`测试翻译键: ${key}`);
        if (typeof window.t === 'function') {
            const result = window.t(key);
            console.log(`结果: ${result}`);
            return result;
        } else {
            console.error('window.t 函数不存在');
            return null;
        }
    };
    
    // 强制切换语言（带详细日志）
    window.forceSetLocale = async function(locale) {
        console.log(`[强制切换] 开始切换到: ${locale}`);
        
        if (typeof window.setLocale !== 'function') {
            console.error('[强制切换] window.setLocale 不存在');
            return false;
        }
        
        if (typeof i18next === 'undefined') {
            console.error('[强制切换] i18next 未加载');
            return false;
        }
        
        console.log(`[强制切换] 当前语言: ${i18next.language}`);
        console.log(`[强制切换] i18next 已初始化: ${i18next.isInitialized}`);
        
        try {
            const result = await window.setLocale(locale);
            console.log(`[强制切换] 切换结果: ${result}`);
            
            // 等待一下，然后检查
            setTimeout(() => {
                console.log(`[强制切换] 切换后语言: ${i18next.language}`);
                console.log(`[强制切换] 测试翻译 voice.title: ${window.t('voice.title')}`);
            }, 100);
            
            return result;
        } catch (error) {
            console.error('[强制切换] 切换失败:', error);
            return false;
        }
    };
    
    // 检查 i18next 是否已加载
    if (typeof i18next === 'undefined') {
        console.error('[i18n] ❌ i18next is not loaded. Please include i18next CDN before this script.');
        console.log('✅ 诊断工具已加载，可以使用 window.diagnoseI18n() 来诊断问题');
        // 即使 i18next 未加载，也导出降级函数
        exportFallbackFunctions();
        return;
    }
    
    // 检查依赖库是否已加载
    if (typeof i18nextBrowserLanguageDetector === 'undefined') {
        console.warn('[i18n] ⚠️ i18nextBrowserLanguageDetector is not loaded. Using fallback functions.');
        console.log('✅ 诊断工具已加载，可以使用 window.diagnoseI18n() 来诊断问题');
        // 导出降级函数，避免页面脚本报错
        exportFallbackFunctions();
        return;
    }
    
    if (typeof i18nextHttpBackend === 'undefined') {
        console.warn('[i18n] ⚠️ i18nextHttpBackend is not loaded.');
        console.log('[i18n] 💡 使用手动加载翻译文件的方式');
        // 不使用 HTTP Backend，改为手动加载翻译文件
        initWithoutHttpBackend();
        return;
    }
    
    /**
     * 不使用 HTTP Backend，手动加载翻译文件
     */
    async function initWithoutHttpBackend() {
        console.log('[i18n] 开始手动加载翻译文件...');
        
        // 加载所有支持的语言
        async function loadAllLanguages() {
            const languages = ['zh-CN', 'en'];
            const resources = {};
            
            for (const lang of languages) {
                try {
                    const response = await fetch(`/static/locales/${lang}.json`);
                    if (response.ok) {
                        const translations = await response.json();
                        resources[lang] = {
                            translation: translations
                        };
                        console.log(`[i18n] ✅ ${lang} 翻译文件加载成功`);
                    } else {
                        console.warn(`[i18n] ⚠️ ${lang} 翻译文件加载失败: ${response.status}`);
                    }
                } catch (error) {
                    console.warn(`[i18n] ⚠️ ${lang} 翻译文件加载失败:`, error);
                }
            }
            
            return resources;
        }
        
        try {
            // 检测语言
            const detectedLang = localStorage.getItem('preferredLocale') || navigator.language || 'zh-CN';
            const lang = detectedLang.startsWith('zh') ? 'zh-CN' : 'en';
            console.log('[i18n] 检测到的语言:', lang);
            
            // 加载所有语言的翻译文件
            const resources = await loadAllLanguages();
            
            if (Object.keys(resources).length === 0) {
                throw new Error('所有翻译文件加载失败');
            }
            
            // 初始化 i18next（不使用 HTTP Backend）
            i18next
                .use(i18nextBrowserLanguageDetector)
                .init({
                    lng: lang,
                    fallbackLng: 'zh-CN',
                    supportedLngs: ['zh-CN', 'en'],
                    ns: ['translation'],
                    defaultNS: 'translation',
                    resources: resources,  // 使用所有已加载的翻译
                    detection: {
                        order: ['localStorage', 'navigator'],
                        lookupLocalStorage: 'preferredLocale',
                        caches: ['localStorage']
                    },
                    interpolation: {
                        escapeValue: false
                    },
                    debug: true
                }, function(err, t) {
                    if (err) {
                        console.error('[i18n] 初始化失败:', err);
                        exportFallbackFunctions();
                        return;
                    }
                    
                    console.log('[i18n] ✅ 初始化成功（手动加载模式）');
                    console.log('[i18n] 当前语言:', i18next.language);
                    
                    // 更新页面文本
                    updatePageTexts();
                    window.dispatchEvent(new CustomEvent('localechange'));
                    
                    // 导出正常函数（包含语言切换功能）
                    exportNormalFunctions();
                });
        } catch (error) {
            console.error('[i18n] 手动加载翻译文件失败:', error);
            exportFallbackFunctions();
        }
    }
    
    /**
     * 导出降级函数（当初始化失败时使用）
     */
    function exportFallbackFunctions() {
        console.warn('[i18n] Using fallback functions due to initialization failure');
        
        // 降级翻译函数
        window.t = function(key, params = {}) {
            console.warn('[i18n] Fallback t() called with key:', key);
            // 返回键名本身，或者尝试从元素获取原始文本
            return key;
        };
        
        // 降级语言切换函数
        window.setLocale = async function(locale) {
            console.warn('[i18n] Fallback setLocale() called with locale:', locale);
            console.error('[i18n] Cannot change language: i18next not initialized');
            return false;
        };
        
        // 降级获取语言函数
        window.getLocale = function() {
            return localStorage.getItem('preferredLocale') || navigator.language || 'zh-CN';
        };
        
        // 降级 i18n 对象
        window.i18n = {
            isInitialized: false,
            language: window.getLocale(),
            store: { data: {} }
        };
        
        // 降级更新函数
        window.updatePageTexts = function() {
            console.warn('[i18n] Fallback updatePageTexts() called - no-op');
        };
        
        window.updateLive2DDynamicTexts = function() {
            console.warn('[i18n] Fallback updateLive2DDynamicTexts() called - no-op');
        };
    }
    
    // 初始化 i18next
    console.log('[i18n] 开始初始化 i18next...');
    console.log('[i18n] i18next 类型:', typeof i18next);
    console.log('[i18n] i18nextBrowserLanguageDetector 类型:', typeof i18nextBrowserLanguageDetector);
    console.log('[i18n] i18nextHttpBackend 类型:', typeof i18nextHttpBackend);
    
    try {
        i18next
            .use(i18nextBrowserLanguageDetector)  // 自动检测浏览器语言
            .use(i18nextHttpBackend)  // 从服务器加载翻译文件
            .init({
            // 回退语言
            fallbackLng: 'zh-CN',
            
            // 支持的语言列表
            supportedLngs: ['zh-CN', 'en'],
            
            // 命名空间（我们的 JSON 文件没有命名空间结构，需要包装）
            ns: ['translation'],
            defaultNS: 'translation',
            
            // 翻译文件路径
            backend: {
                loadPath: '/static/locales/{{lng}}.json',
                // 自定义解析函数：将 JSON 文件包装在 'translation' 命名空间下
                parse: function(data) {
                    const parsed = JSON.parse(data);
                    // 返回包装后的对象，i18next 会将其放在 'translation' 命名空间下
                    return { translation: parsed };
                }
            },
            
            // 语言检测选项
            detection: {
                // 检测顺序：localStorage > navigator
                order: ['localStorage', 'navigator'],
                // localStorage 键名（与现有代码保持一致）
                lookupLocalStorage: 'preferredLocale',
                // 缓存用户选择
                caches: ['localStorage']
            },
            
            // 插值选项
            interpolation: {
                escapeValue: false  // HTML 不需要转义
            },
            
            // 调试模式（开发时可以开启）
            debug: true  // 开启调试，方便查看语言检测过程
        }, function(err, t) {
            console.log('[i18n] 初始化回调被调用');
            console.log('[i18n] err:', err);
            console.log('[i18n] t:', typeof t);
            console.log('[i18n] i18next.isInitialized:', i18next?.isInitialized);
            
            if (err) {
                console.error('[i18n] Initialization failed:', err);
                console.error('[i18n] Error details:', err.stack || err);
                console.error('[i18n] Error message:', err.message);
                console.error('[i18n] Error name:', err.name);
                // 即使初始化失败，也要导出函数（使用降级方案）
                exportFallbackFunctions();
                return;
            }
            
            console.log('[i18n] ✅ 初始化成功！');
            console.log('[i18n] Initialized with locale:', i18next.language);
            console.log('[i18n] Browser language:', navigator.language);
            console.log('[i18n] LocalStorage preferredLocale:', localStorage.getItem('preferredLocale'));
            console.log('[i18n] Detection order: localStorage -> navigator -> fallback');
            console.log('[i18n] i18next.isInitialized:', i18next.isInitialized);
            
            // 初始化完成后更新页面文本
            updatePageTexts();
            
            // 触发自定义事件，通知其他脚本
            window.dispatchEvent(new CustomEvent('localechange'));
            
            // 初始化成功后导出正常函数
            exportNormalFunctions();
        });
        
        console.log('[i18n] init() 调用完成，等待回调...');
    } catch (error) {
        console.error('[i18n] Fatal error during initialization:', error);
        console.error('[i18n] Error stack:', error.stack);
        // 即使出错，也要导出降级函数
        exportFallbackFunctions();
    }
    
    /**
     * 导出正常函数（初始化成功后使用）
     */
    function exportNormalFunctions() {
        // 导出翻译函数，保持与现有代码兼容
        window.t = function(key, params = {}) {
            if (!key) return '';
            
            // 处理 providerKey 参数（与现有代码兼容）
            if (params && params.providerKey) {
                const providerKey = params.providerKey;
                const resources = i18next.getResourceBundle(i18next.language, 'translation');
                const providerNames = resources?.api?.providerNames || {};
                const providerName = providerNames[providerKey];
                params.provider = providerName || providerKey;
            }
            
            return i18next.t(key, params);
        };
        
        // 导出语言切换函数
        window.setLocale = async function(locale) {
            console.log('[i18n] setLocale called with:', locale);
            console.log('[i18n] Current language before change:', i18next.language);
            
            try {
                // 切换语言
                await i18next.changeLanguage(locale);
                console.log('[i18n] Language changed to:', i18next.language);
                
                // 更新页面文本
                updatePageTexts();
                updateLive2DDynamicTexts();
                
                // 触发自定义事件
                window.dispatchEvent(new CustomEvent('localechange'));
                
                console.log('[i18n] Page texts updated');
                return true;
            } catch (error) {
                console.error('[i18n] Failed to change language:', error);
                return false;
            }
        };
        
        // 导出获取当前语言函数
        window.getLocale = function() {
            return i18next.language;
        };
        
        // 导出 i18next 实例，方便高级用法
        window.i18n = i18next;
        
        // 导出更新函数
        window.updatePageTexts = updatePageTexts;
        window.updateLive2DDynamicTexts = updateLive2DDynamicTexts;
        window.translateStatusMessage = translateStatusMessage;
        
        // 监听语言变化
        i18next.on('languageChanged', () => {
            updatePageTexts();
            updateLive2DDynamicTexts();
            window.dispatchEvent(new CustomEvent('localechange'));
        });
        
        // 确保在 DOM 加载完成后更新文本
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
                updatePageTexts();
                updateLive2DDynamicTexts();
            });
        } else {
            updatePageTexts();
            updateLive2DDynamicTexts();
        }
        
        console.log('[i18n] Normal functions exported successfully');
    }
    
    /**
     * 更新页面文本的函数
     * 保持与现有代码兼容
     */
    function updatePageTexts() {
        console.log('[i18n] updatePageTexts called, current language:', i18next.language);
        console.log('[i18n] i18next.isInitialized:', i18next.isInitialized);
        
        // 检查 i18next 是否已初始化
        if (!i18next.isInitialized) {
            console.warn('[i18n] i18next not initialized yet, skipping updatePageTexts');
            return;
        }
        
        // 更新所有带有 data-i18n 属性的元素
        const elements = document.querySelectorAll('[data-i18n]');
        console.log(`[i18n] Found ${elements.length} elements with data-i18n attribute`);
        
        elements.forEach(element => {
            const key = element.getAttribute('data-i18n');
            let params = {};
            
            if (element.hasAttribute('data-i18n-params')) {
                try {
                    params = JSON.parse(element.getAttribute('data-i18n-params'));
                } catch (e) {
                    console.warn(`[i18n] Failed to parse params for ${key}:`, e);
                }
            }
            
            // 处理 providerKey 参数（与现有代码兼容）
            if (params.providerKey) {
                const providerKey = params.providerKey;
                // 从 i18next 的资源中获取 providerNames
                const resources = i18next.getResourceBundle(i18next.language, 'translation');
                const providerNames = resources?.api?.providerNames || {};
                const providerName = providerNames[providerKey];
                params.provider = providerName || providerKey;
            }
            
            const text = i18next.t(key, params);
            
            // 如果翻译失败（返回键名本身），记录警告但继续
            if (text === key) {
                console.warn(`[i18n] Translation key not found: ${key}`);
            }
            
            // 特殊处理 title 标签
            if (element.tagName === 'TITLE') {
                document.title = text;
                return;
            }
            
            // 更新文本内容
            element.textContent = text;
        });
        
        // 更新所有带有 data-i18n-placeholder 属性的元素
        document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
            const key = element.getAttribute('data-i18n-placeholder');
            const text = i18next.t(key, {});
            if (text && text !== key) {
                element.placeholder = text;
            }
        });
        
        // 更新所有带有 data-i18n-title 属性的元素
        document.querySelectorAll('[data-i18n-title]').forEach(element => {
            const key = element.getAttribute('data-i18n-title');
            const text = i18next.t(key, {});
            if (text && text !== key) {
                element.title = text;
            }
        });
        
        console.log('[i18n] updatePageTexts completed');
    }
    
    /**
     * 更新 Live2D 动态文本
     * 保持与现有代码兼容
     */
    function updateLive2DDynamicTexts() {
        // 更新浮动按钮的标题
        const buttons = document.querySelectorAll('.floating-btn');
        buttons.forEach(btn => {
            const titleKey = btn.getAttribute('data-i18n-title');
            if (titleKey) {
                btn.title = i18next.t(titleKey);
            }
        });
        
        // 更新设置菜单项
        const menuItems = document.querySelectorAll('[data-i18n-label]');
        menuItems.forEach(item => {
            const labelKey = item.getAttribute('data-i18n-label');
            if (labelKey) {
                const label = item.querySelector('label');
                if (label) {
                    label.textContent = i18next.t(labelKey);
                }
            }
        });
        
        // 更新动态创建的标签
        document.querySelectorAll('[data-i18n]').forEach(element => {
            const key = element.getAttribute('data-i18n');
            if (key && element._updateLabelText) {
                element._updateLabelText();
            }
        });
    }
    
    /**
     * 翻译状态消息
     * 保持与现有代码兼容
     */
    function translateStatusMessage(message) {
        if (!message || typeof message !== 'string') return message;
        
        // 定义消息映射规则
        const messageMap = [
            {
                pattern: /启动超时/i,
                translator: () => i18next.t('app.sessionTimeout')
            },
            {
                pattern: /无法连接/i,
                translator: () => i18next.t('app.websocketNotConnectedError')
            },
            {
                pattern: /Session启动失败/i,
                translator: () => i18next.t('app.sessionStartFailed')
            },
            {
                pattern: /记忆服务器.*崩溃/i,
                translator: (match) => {
                    const portMatch = match.match(/端口(\d+)/);
                    return i18next.t('app.memoryServerCrashed', { port: portMatch ? portMatch[1] : 'unknown' });
                }
            }
        ];
        
        for (const { pattern, translator } of messageMap) {
            if (pattern.test(message)) {
                return translator(message);
            }
        }
        
        return message;
    }
    
    // 诊断工具函数已在文件开头定义，这里只输出提示信息
    console.log('✅ i18next 诊断工具已加载！');
    console.log('使用以下命令：');
    console.log('  - window.diagnoseI18n()      // 诊断 i18next 状态');
    console.log('  - window.testTranslation("voice.title")  // 测试翻译');
    console.log('  - window.forceSetLocale("en")  // 强制切换语言（带详细日志）');
})();

