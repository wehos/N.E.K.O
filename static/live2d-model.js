/**
 * Live2D Model - 模型加载、口型同步相关功能
 */

// 加载模型
Live2DManager.prototype.loadModel = async function(modelPath, options = {}) {
    if (!this.pixi_app) {
        throw new Error('PIXI 应用未初始化，请先调用 initPIXI()');
    }

    // 移除当前模型
    if (this.currentModel) {
        // 先清空常驻表情记录和初始参数
        this.teardownPersistentExpressions();
        this.initialParameters = {};

        // 还原 coreModel.update 覆盖
        try {
            const coreModel = this.currentModel.internalModel && this.currentModel.internalModel.coreModel;
            if (coreModel && this._mouthOverrideInstalled && typeof this._origCoreModelUpdate === 'function') {
                coreModel.update = this._origCoreModelUpdate;
            }
        } catch (_) {}
        this._mouthOverrideInstalled = false;
        this._origCoreModelUpdate = null;
        // 同时移除 mouthTicker（若曾启用过 ticker 模式）
        if (this._mouthTicker && this.pixi_app && this.pixi_app.ticker) {
            try { this.pixi_app.ticker.remove(this._mouthTicker); } catch (_) {}
            this._mouthTicker = null;
        }

        // 移除由 HTML 锁图标或交互注册的监听，避免访问已销毁的显示对象
        try {
            // 先移除锁图标的 ticker 回调
            if (this._lockIconTicker && this.pixi_app && this.pixi_app.ticker) {
                this.pixi_app.ticker.remove(this._lockIconTicker);
            }
            this._lockIconTicker = null;
            // 移除锁图标元素
            if (this._lockIconElement && this._lockIconElement.parentNode) {
                this._lockIconElement.parentNode.removeChild(this._lockIconElement);
            }
            this._lockIconElement = null;
            
            // 清理浮动按钮系统
            if (this._floatingButtonsTicker && this.pixi_app && this.pixi_app.ticker) {
                this.pixi_app.ticker.remove(this._floatingButtonsTicker);
            }
            this._floatingButtonsTicker = null;
            if (this._floatingButtonsContainer && this._floatingButtonsContainer.parentNode) {
                this._floatingButtonsContainer.parentNode.removeChild(this._floatingButtonsContainer);
            }
            this._floatingButtonsContainer = null;
            this._floatingButtons = {};
            // 清理"请她回来"按钮容器
            if (this._returnButtonContainer && this._returnButtonContainer.parentNode) {
                this._returnButtonContainer.parentNode.removeChild(this._returnButtonContainer);
            }
            this._returnButtonContainer = null;
            // 清理所有弹出框定时器
            Object.values(this._popupTimers).forEach(timer => clearTimeout(timer));
            this._popupTimers = {};
            
            // 暂停 ticker，期间做销毁，随后恢复
            this.pixi_app.ticker && this.pixi_app.ticker.stop();
        } catch (_) {}
        try {
            this.pixi_app.stage.removeAllListeners && this.pixi_app.stage.removeAllListeners();
        } catch (_) {}
        try {
            this.currentModel.removeAllListeners && this.currentModel.removeAllListeners();
        } catch (_) {}

        // 从舞台移除并销毁旧模型
        try { this.pixi_app.stage.removeChild(this.currentModel); } catch (_) {}
        try { this.currentModel.destroy({ children: true }); } catch (_) {}
        try { this.pixi_app.ticker && this.pixi_app.ticker.start(); } catch (_) {}
    }

    try {
        const model = await Live2DModel.from(modelPath, { autoInteract: false });
        this.currentModel = model;

        // 解析模型目录名与根路径，供资源解析使用
        try {
            let urlString = null;
            if (typeof modelPath === 'string') {
                urlString = modelPath;
            } else if (modelPath && typeof modelPath === 'object' && typeof modelPath.url === 'string') {
                urlString = modelPath.url;
            }

            if (typeof urlString !== 'string') throw new TypeError('modelPath/url is not a string');

            // 记录用于保存偏好的原始模型路径（供 beforeunload 使用）
            try { this._lastLoadedModelPath = urlString; } catch (_) {}

            const cleanPath = urlString.split('#')[0].split('?')[0];
            const lastSlash = cleanPath.lastIndexOf('/');
            const rootDir = lastSlash >= 0 ? cleanPath.substring(0, lastSlash) : '/static';
            this.modelRootPath = rootDir; // e.g. /static/mao_pro or /static/some/deeper/dir
            const parts = rootDir.split('/').filter(Boolean);
            this.modelName = parts.length > 0 ? parts[parts.length - 1] : null;
            console.log('模型根路径解析:', { modelUrl: urlString, modelName: this.modelName, modelRootPath: this.modelRootPath });
        } catch (e) {
            console.warn('解析模型根路径失败，将使用默认值', e);
            this.modelRootPath = '/static';
            this.modelName = null;
        }

        // 配置渲染纹理数量以支持更多蒙版
        if (model.internalModel && model.internalModel.renderer && model.internalModel.renderer._clippingManager) {
            model.internalModel.renderer._clippingManager._renderTextureCount = 3;
            if (typeof model.internalModel.renderer._clippingManager.initialize === 'function') {
                model.internalModel.renderer._clippingManager.initialize(
                    model.internalModel.coreModel,
                    model.internalModel.coreModel.getDrawableCount(),
                    model.internalModel.coreModel.getDrawableMasks(),
                    model.internalModel.coreModel.getDrawableMaskCounts(),
                    3
                );
            }
            console.log('渲染纹理数量已设置为3');
        }

        // 应用位置和缩放设置
        this.applyModelSettings(model, options);

        // 添加到舞台
        this.pixi_app.stage.addChild(model);

        // 设置交互性
        if (options.dragEnabled !== false) {
            this.setupDragAndDrop(model);
        }

        // 设置滚轮缩放
        if (options.wheelEnabled !== false) {
            this.setupWheelZoom(model);
        }
        
        // 设置触摸缩放（双指捏合）
        if (options.touchZoomEnabled !== false) {
            this.setupTouchZoom(model);
        }

        // 启用鼠标跟踪
        if (options.mouseTracking !== false) {
            this.enableMouseTracking(model);
        }

        // 设置浮动按钮系统（在模型完全就绪后再绑定ticker回调）
        this.setupFloatingButtons(model);
        
        // 设置原来的锁按钮
        this.setupHTMLLockIcon(model);

        // 安装口型覆盖逻辑（屏蔽 motion 对嘴巴的控制）
        try {
            this.installMouthOverride();
            console.log('已安装口型覆盖');
        } catch (e) {
            console.warn('安装口型覆盖失败:', e);
        }

        // 加载 FileReferences 与 EmotionMapping
        if (options.loadEmotionMapping !== false) {
            const settings = model.internalModel && model.internalModel.settings && model.internalModel.settings.json;
            if (settings) {
                // 保存原始 FileReferences
                this.fileReferences = settings.FileReferences || null;

                // 优先使用顶层 EmotionMapping，否则从 FileReferences 推导
                if (settings.EmotionMapping && (settings.EmotionMapping.expressions || settings.EmotionMapping.motions)) {
                    this.emotionMapping = settings.EmotionMapping;
                } else {
                    this.emotionMapping = this.deriveEmotionMappingFromFileRefs(this.fileReferences || {});
                }
                console.log('已加载情绪映射:', this.emotionMapping);
            } else {
                console.warn('模型配置中未找到 settings.json，无法加载情绪映射');
            }
        }

        // 先从服务器同步映射（覆盖"常驻"），再设置常驻表情
        try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
        // 设置常驻表情（根据 EmotionMapping.expressions.常驻 或 FileReferences 前缀推导）
        await this.setupPersistentExpressions();

        // 记录模型的初始参数（用于expression重置）
        this.recordInitialParameters();

        // 调用回调函数
        if (this.onModelLoaded) {
            this.onModelLoaded(model, modelPath);
        }

        return model;
    } catch (error) {
        console.error('加载模型失败:', error);
        
        // 尝试回退到默认模型
        if (modelPath !== '/static/mao_pro/mao_pro.model3.json') {
            console.warn('模型加载失败，尝试回退到默认模型: mao_pro');
            try {
                const defaultModelPath = '/static/mao_pro/mao_pro.model3.json';
                const model = await Live2DModel.from(defaultModelPath, { autoInteract: false });
                this.currentModel = model;

                // 解析模型目录名与根路径，供资源解析使用
                try {
                    const cleanPath = defaultModelPath.split('#')[0].split('?')[0];
                    const lastSlash = cleanPath.lastIndexOf('/');
                    const rootDir = lastSlash >= 0 ? cleanPath.substring(0, lastSlash) : '/static';
                    this.modelRootPath = rootDir;
                    const parts = rootDir.split('/').filter(Boolean);
                    this.modelName = parts.length > 0 ? parts[parts.length - 1] : null;
                    console.log('回退模型根路径解析:', { modelUrl: defaultModelPath, modelName: this.modelName, modelRootPath: this.modelRootPath });
                    try { this._lastLoadedModelPath = defaultModelPath; } catch (_) {}
                } catch (e) {
                    console.warn('解析回退模型根路径失败，将使用默认值', e);
                    this.modelRootPath = '/static';
                    this.modelName = null;
                }

                // 配置渲染纹理数量以支持更多蒙版
                if (model.internalModel && model.internalModel.renderer && model.internalModel.renderer._clippingManager) {
                    model.internalModel.renderer._clippingManager._renderTextureCount = 3;
                    if (typeof model.internalModel.renderer._clippingManager.initialize === 'function') {
                        model.internalModel.renderer._clippingManager.initialize(
                            model.internalModel.coreModel,
                            model.internalModel.coreModel.getDrawableCount(),
                            model.internalModel.coreModel.getDrawableMasks(),
                            model.internalModel.coreModel.getDrawableMaskCounts(),
                            3
                        );
                    }
                    console.log('回退模型渲染纹理数量已设置为3');
                }

                // 应用位置和缩放设置
                this.applyModelSettings(model, options);

                // 添加到舞台
                this.pixi_app.stage.addChild(model);

                // 设置交互性
                if (options.dragEnabled !== false) {
                    this.setupDragAndDrop(model);
                }

                // 设置滚轮缩放
                if (options.wheelEnabled !== false) {
                    this.setupWheelZoom(model);
                }
                
                // 设置触摸缩放（双指捏合）
                if (options.touchZoomEnabled !== false) {
                    this.setupTouchZoom(model);
                }

                // 启用鼠标跟踪
                if (options.mouseTracking !== false) {
                    this.enableMouseTracking(model);
                }

                // 设置浮动按钮系统（在模型完全就绪后再绑定ticker回调）
                this.setupFloatingButtons(model);
                
                // 设置原来的锁按钮
                this.setupHTMLLockIcon(model);

                // 安装口型覆盖逻辑（屏蔽 motion 对嘴巴的控制）
                try {
                    this.installMouthOverride();
                    console.log('回退模型已安装口型覆盖');
                } catch (e) {
                    console.warn('回退模型安装口型覆盖失败:', e);
                }

                // 加载 FileReferences 与 EmotionMapping
                if (options.loadEmotionMapping !== false) {
                    const settings = model.internalModel && model.internalModel.settings && model.internalModel.settings.json;
                    if (settings) {
                        // 保存原始 FileReferences
                        this.fileReferences = settings.FileReferences || null;

                        // 优先使用顶层 EmotionMapping，否则从 FileReferences 推导
                        if (settings.EmotionMapping && (settings.EmotionMapping.expressions || settings.EmotionMapping.motions)) {
                            this.emotionMapping = settings.EmotionMapping;
                        } else {
                            this.emotionMapping = this.deriveEmotionMappingFromFileRefs(this.fileReferences || {});
                        }
                        console.log('回退模型已加载情绪映射:', this.emotionMapping);
                    } else {
                        console.warn('回退模型配置中未找到 settings.json，无法加载情绪映射');
                    }
                }

                // 先从服务器同步映射（覆盖"常驻"），再设置常驻表情
                try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
                // 设置常驻表情（根据 EmotionMapping.expressions.常驻 或 FileReferences 前缀推导）
                await this.setupPersistentExpressions();

                // 调用回调函数
                if (this.onModelLoaded) {
                    this.onModelLoaded(model, defaultModelPath);
                }

                console.log('成功回退到默认模型: mao_pro');
                return model;
            } catch (fallbackError) {
                console.error('回退到默认模型也失败:', fallbackError);
                throw new Error(`原始模型加载失败: ${error.message}，且回退模型也失败: ${fallbackError.message}`);
            }
        } else {
            // 如果已经是默认模型，直接抛出错误
            throw error;
        }
    }
};

// 不再需要预解析嘴巴参数ID，保留占位以兼容旧代码调用
Live2DManager.prototype.resolveMouthParameterId = function() { return null; };

// 安装覆盖：覆盖 coreModel.update 方法，在 SDK 程序化动画之后强制写入参数
// 这是最可靠的方式，因为 coreModel.update 是在所有参数修改之后、渲染之前调用的
Live2DManager.prototype.installMouthOverride = function() {
    if (!this.currentModel || !this.currentModel.internalModel) {
        throw new Error('模型未就绪，无法安装口型覆盖');
    }

    const internalModel = this.currentModel.internalModel;
    const coreModel = internalModel.coreModel;
    
    if (!coreModel) {
        throw new Error('coreModel 不可用');
    }

    // 如果之前装过，先还原
    if (this._mouthOverrideInstalled && typeof this._origCoreModelUpdate === 'function') {
        try { coreModel.update = this._origCoreModelUpdate; } catch (_) {}
        this._origCoreModelUpdate = null;
    }

    // 口型参数列表（这些参数不会被常驻表情覆盖）
    const lipSyncParams = ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
    
    // 保存原始的 coreModel.update 方法
    const origCoreModelUpdate = coreModel.update ? coreModel.update.bind(coreModel) : null;
    this._origCoreModelUpdate = origCoreModelUpdate;
    
    // 缓存参数索引，避免每帧查询
    const mouthParamIndices = {};
    for (const id of ['ParamMouthOpenY', 'ParamO']) {
        try {
            const idx = coreModel.getParameterIndex(id);
            if (idx >= 0) mouthParamIndices[id] = idx;
        } catch (_) {}
    }
    
    // 覆盖 coreModel.update 方法
    // 在调用原始 update 之前写入参数（因为 update 会将参数应用到模型）
    coreModel.update = () => {
        try {
            // 1. 强制写入口型参数（使用索引直接设置）
            for (const [id, idx] of Object.entries(mouthParamIndices)) {
                try {
                    coreModel.setParameterValueByIndex(idx, this.mouthValue);
                } catch (_) {}
            }
            
            // 2. 强制写入常驻表情参数（跳过口型参数）
            if (this.persistentExpressionParamsByName) {
                for (const name of (this.persistentExpressionNames || [])) {
                    const params = this.persistentExpressionParamsByName[name];
                    if (Array.isArray(params)) {
                        for (const p of params) {
                            // 跳过口型参数
                            if (lipSyncParams.includes(p.Id)) continue;
                            try {
                                const idx = coreModel.getParameterIndex(p.Id);
                                if (idx >= 0) {
                                    coreModel.setParameterValueByIndex(idx, p.Value);
                                }
                            } catch (_) {}
                        }
                    }
                }
            }
        } catch (e) {
            // 静默处理错误
        }
        
        // 调用原始的 update 方法（将参数应用到模型顶点）
        if (origCoreModelUpdate) {
            origCoreModelUpdate();
        }
    };

    this._mouthOverrideInstalled = true;
    console.log('已安装参数覆盖（口型 + 常驻表情），使用 coreModel.update 前置覆盖方式');
};

// 设置嘴巴开合值（0~1）
Live2DManager.prototype.setMouth = function(value) {
    const v = Math.max(0, Math.min(1, Number(value) || 0));
    this.mouthValue = v;
    // 即时写入一次，best-effort 同步
    try {
        if (this.currentModel && this.currentModel.internalModel) {
            const coreModel = this.currentModel.internalModel.coreModel;
            const mouthIds = ['ParamMouthOpenY', 'ParamO'];
            for (const id of mouthIds) {
                try {
                    if (coreModel.getParameterIndex(id) !== -1) {
                        coreModel.setParameterValueById(id, this.mouthValue, 1);
                    }
                } catch (_) {}
            }
        }
    } catch (_) {}
};

// 应用模型设置
Live2DManager.prototype.applyModelSettings = function(model, options) {
    const { preferences, isMobile = false } = options;

    if (isMobile) {
        // 移动端设置
        const scale = Math.min(
            0.5,
            window.innerHeight * 1.3 / 4000,
            window.innerWidth * 1.2 / 2000
        );
        model.scale.set(scale);
        model.x = this.pixi_app.renderer.width * 0.5;
        model.y = this.pixi_app.renderer.height * 0.28;
        model.anchor.set(0.5, 0.1);
    } else {
        // 桌面端设置
        if (preferences && preferences.scale && preferences.position) {
            // 使用保存的偏好设置
            model.scale.set(preferences.scale.x, preferences.scale.y);
            model.x = preferences.position.x;
            model.y = preferences.position.y;
        } else {
            // 使用默认设置（改为靠屏幕右侧）
            const scale = Math.min(
                0.5,
                (window.innerHeight * 0.75) / 7000,
                (window.innerWidth * 0.6) / 7000
            );
            model.scale.set(scale);
            // 将默认 x 调整到屏幕靠右位置，使用 0.85 作为右侧偏移比例
            // 向右下角进一步偏移，靠近屏幕右下
            model.x = this.pixi_app.renderer.width;
            model.y = this.pixi_app.renderer.height;
        }
        // 增大 anchor.x 以便模型更靠近右侧边缘
        model.anchor.set(0.65, 0.75);
    }
};

