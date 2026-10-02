document.addEventListener("DOMContentLoaded", function() {
    var forms = document.querySelectorAll(".interpretation-room-form");
    forms.forEach(function(form) {
        if (form.dataset.initialized) return;
        form.dataset.initialized = "true";
        initRoomConfigureForm(form);
    });

    function initRoomConfigureForm(form) {
        var interpreterSelect = form.querySelector('[name$="-interpreter"]');
        var enableRoom = form.querySelector('[name$="-room_enabled"]');
        var enableTranscription = form.querySelector('[name$="-enable_transcription"]');
        var enableTranslation = form.querySelector('[name$="-enable_translation"]');
        var aiConfigs = form.querySelectorAll('.voxbento-ai-config');

        var transcriptionFields = form.querySelector('.transcription-fields');
        var translationFields = form.querySelector('.translation-fields');

        var configuredKeysRaw = form.getAttribute('data-configured-keys');
        var configuredKeys = {};
        if (configuredKeysRaw) {
            try {
                configuredKeys = JSON.parse(configuredKeysRaw);
            } catch (e) {
                console.error("Failed to parse configured keys", e);
            }
        }

        var invalidKeysRaw = form.getAttribute('data-invalid-keys');
        var invalidKeys = {};
        if (invalidKeysRaw) {
            try {
                invalidKeys = JSON.parse(invalidKeysRaw);
            } catch (e) {
                console.error("Failed to parse invalid keys", e);
            }
        }

        function updateVisibility() {
            var isVoxbento = interpreterSelect && interpreterSelect.value === 'voxbento';
            var isRoomEnabled = enableRoom ? enableRoom.checked : false;
            
            aiConfigs.forEach(function(el) {
                el.style.display = (isVoxbento && isRoomEnabled) ? 'block' : 'none';
            });

            if (isVoxbento) {
                var transOn = enableTranscription && enableTranscription.checked;
                var translOn = enableTranslation && enableTranslation.checked;

                // Toggle disabled states for transcription inputs
                if (transcriptionFields) {
                    var tInputs = transcriptionFields.querySelectorAll('input, select');
                    tInputs.forEach(function(inp) {
                        inp.disabled = !transOn;
                    });
                }

                // Force disable translation if transcription is off
                if (enableTranslation && !transOn) {
                    enableTranslation.checked = false;
                    enableTranslation.disabled = true;
                    translOn = false;
                } else if (enableTranslation) {
                    enableTranslation.disabled = false;
                }

                // Toggle disabled states for translation inputs
                if (translationFields) {
                    var trInputs = translationFields.querySelectorAll('input, select');
                    trInputs.forEach(function(inp) {
                        inp.disabled = !translOn;
                    });
                }

                if (typeof updateAPIKeys === 'function') {
                    var transProviderSelectEl = form.querySelector('[name$="-transcription_provider"]');
                    var translProviderSelectEl = form.querySelector('[name$="-translation_provider"]');
                    if (transProviderSelectEl) updateAPIKeys(transProviderSelectEl, 'transcription');
                    if (translProviderSelectEl) updateAPIKeys(translProviderSelectEl, 'translation');
                }
            }
        }

        if (interpreterSelect) {
            interpreterSelect.addEventListener('change', updateVisibility);
        }
        if (enableRoom) {
            enableRoom.addEventListener('change', updateVisibility);
        }
        if (enableTranscription) {
            enableTranscription.addEventListener('change', updateVisibility);
        }
        if (enableTranslation) {
            enableTranslation.addEventListener('change', updateVisibility);
        }

        // Initial run
        updateVisibility();

        // Cascading dropdowns for Providers and Models
        var transProviderSelect = form.querySelector('[name$="-transcription_provider"]');
        var transModelSelect = form.querySelector('[name$="-transcription_model"]');
        var masterTransModelSelect = transModelSelect ? transModelSelect.cloneNode(true) : null;
        
        var sourceLangSelect = form.querySelector('[name$="-source_language"]');
        if (sourceLangSelect && typeof window.jQuery !== 'undefined' && window.jQuery.fn.select2) {
            window.jQuery(sourceLangSelect).select2({
                width: '100%',
                theme: 'bootstrap'
            });
        }

        var translProviderSelect = form.querySelector('[name$="-translation_provider"]');
        var translModelSelect = form.querySelector('[name$="-translation_model"]');
        var masterTranslModelSelect = translModelSelect ? translModelSelect.cloneNode(true) : null;

        var transProviderIndexMap = {
            'local': 0,
            'openai': 1,
            'deepgram': 2,
            'nvidia': 3,
            'elevenlabs': 4
        };

        var translProviderIndexMap = {
            'local': 0,
            'openai': 1,
            'openrouter': 2,
            'gemini': 3,
            'anthropic': 4,
            'groq': 5
        };

        function cascadeModels(providerSelect, modelSelect, masterModelSelect, indexMap) {
            if (!providerSelect || !modelSelect || !masterModelSelect) return;
            
            var currentVal = modelSelect.value;
            modelSelect.innerHTML = '';
            
            var defaultOpt = masterModelSelect.querySelector('option[value=""]');
            if (defaultOpt) modelSelect.appendChild(defaultOpt.cloneNode(true));
            
            var providerValue = providerSelect.value;
            if (providerValue && indexMap[providerValue] !== undefined) {
                var groupIndex = indexMap[providerValue];
                var optgroups = masterModelSelect.querySelectorAll('optgroup');
                var optgroup = optgroups[groupIndex];
                if (optgroup) {
                    modelSelect.appendChild(optgroup.cloneNode(true));
                }
            }
            
            var options = Array.from(modelSelect.options);
            var stillExists = options.some(function(opt) { return opt.value === currentVal; });
            
            if (stillExists && currentVal !== "") {
                modelSelect.value = currentVal;
            } else {
                // Auto-select if there is exactly 1 valid option (plus the default empty one)
                if (options.length === 2 && options[0].value === "") {
                    modelSelect.value = options[1].value;
                } else {
                    modelSelect.value = "";
                }
            }
        }

        if (transProviderSelect) {
            transProviderSelect.addEventListener('change', function() {
                cascadeModels(transProviderSelect, transModelSelect, masterTransModelSelect, transProviderIndexMap);
            });
            cascadeModels(transProviderSelect, transModelSelect, masterTransModelSelect, transProviderIndexMap);
        }

        if (translProviderSelect) {
            translProviderSelect.addEventListener('change', function() {
                cascadeModels(translProviderSelect, translModelSelect, masterTranslModelSelect, translProviderIndexMap);
                updateAPIKeys(translProviderSelect, 'translation');
            });
            cascadeModels(translProviderSelect, translModelSelect, masterTranslModelSelect, translProviderIndexMap);
            updateAPIKeys(translProviderSelect, 'translation');
        }

        function updateAPIKeys(providerSelect, purpose) {
            if (!providerSelect) return;
            var provider = providerSelect.value;
            var container = form.querySelector('.api-key-container[data-purpose="' + purpose + '"]');
            if (!container) return;
            
            var isFeatureEnabled = false;
            if (purpose === 'transcription' && enableTranscription) {
                isFeatureEnabled = enableTranscription.checked;
            } else if (purpose === 'translation' && enableTranslation) {
                isFeatureEnabled = enableTranslation.checked;
            }
            
            if (isFeatureEnabled && provider && provider !== 'local') {
                container.style.display = 'block';
                var statusDiv = container.querySelector('.api-key-status');
                var inputDiv = container.querySelector('.api-key-input');
                var inputField = inputDiv.querySelector('input');
                
                var keyMapName = provider;
                if (purpose === 'translation' && provider === 'openai') {
                    keyMapName = 'translation_openai';
                }
                
                var prefix = providerSelect.name.replace(/-(?:transcription|translation)_provider$/, '-');
                inputField.name = prefix + keyMapName + '_api_key';
                
                var isConfigured = configuredKeys[keyMapName];
                var isInvalid = invalidKeys[keyMapName];
                var cancelButton = inputDiv.querySelector('.btn-cancel-update');
                var updateInlineButton = inputDiv.querySelector('.btn-save-key-inline');
                
                if (isConfigured && !inputField.dataset.wantsUpdate && !isInvalid) {
                    statusDiv.style.display = 'block';
                    inputDiv.style.display = 'none';
                    inputField.required = false;
                } else {
                    statusDiv.style.display = 'none';
                    inputDiv.style.display = 'block';
                    inputField.required = true;
                    if (isConfigured && !isInvalid) {
                        if (cancelButton) cancelButton.style.display = 'inline-block';
                        if (updateInlineButton) updateInlineButton.style.display = 'inline-block';
                    } else {
                        if (cancelButton) cancelButton.style.display = 'none';
                        if (updateInlineButton) updateInlineButton.style.display = 'none';
                    }
                }
            } else {
                container.style.display = 'none';
                var inputDiv = container.querySelector('.api-key-input');
                var inputField = inputDiv.querySelector('input');
                inputField.name = '';
                inputField.required = false;
            }
        }
        
        var cancelBtns = form.querySelectorAll('.btn-cancel-update');
        cancelBtns.forEach(function(btn) {
            btn.addEventListener('click', function() {
                var container = btn.closest('.api-key-container');
                var statusDiv = container.querySelector('.api-key-status');
                var inputDiv = container.querySelector('.api-key-input');
                var inputField = inputDiv.querySelector('input');
                
                inputField.dataset.wantsUpdate = '';
                inputField.value = '';
                statusDiv.style.display = 'block';
                inputDiv.style.display = 'none';
                inputField.required = false;
            });
        });

        var updateBtns = form.querySelectorAll('.btn-update-key');
        updateBtns.forEach(function(btn) {
            btn.addEventListener('click', function() {
                var container = btn.closest('.api-key-container');
                var statusDiv = container.querySelector('.api-key-status');
                var inputDiv = container.querySelector('.api-key-input');
                var inputField = inputDiv.querySelector('input');
                var cancelBtn = inputDiv.querySelector('.btn-cancel-update');
                var updateInlineBtn = inputDiv.querySelector('.btn-save-key-inline');
                
                inputField.dataset.wantsUpdate = 'true';
                statusDiv.style.display = 'none';
                inputDiv.style.display = 'block';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                if (updateInlineBtn) updateInlineBtn.style.display = 'inline-block';
                inputField.focus();
            });
        });

        var toggleBtns = form.querySelectorAll('.toggle-password');
        toggleBtns.forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                var input = btn.previousElementSibling;
                if (input && input.tagName === 'INPUT') {
                    if (input.type === 'password') {
                        input.type = 'text';
                        btn.innerHTML = '<i class="fa fa-eye-slash"></i>';
                    } else {
                        input.type = 'password';
                        btn.innerHTML = '<i class="fa fa-eye"></i>';
                    }
                }
            });
        });

        if (transProviderSelect) {
            transProviderSelect.addEventListener('change', function() {
                updateAPIKeys(transProviderSelect, 'transcription');
            });
            updateAPIKeys(transProviderSelect, 'transcription');
        }
    }
});
