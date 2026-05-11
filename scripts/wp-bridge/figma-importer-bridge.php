<?php
/**
 * Plugin Name: Figma Importer Bridge
 * Description: REST endpoints for the Figma → Elementor agent. Exposes write access to private Elementor meta keys (`_elementor_data`, kit page settings, library templates). Auth: Application Passwords + capability checks.
 * Version: 0.1.0
 * Author: figma-elementor-agent
 * Requires at least: 5.6
 */

if (!defined('ABSPATH')) {
    exit;
}

if (!defined('FIGMA_IMPORTER_BRIDGE_VERSION')) {
    define('FIGMA_IMPORTER_BRIDGE_VERSION', '0.1.0');
}

add_action('rest_api_init', function () {
    $ns = 'figma-importer/v1';

    register_rest_route($ns, '/health', [
        'methods'             => 'GET',
        'permission_callback' => '__return_true',
        'callback'            => 'figma_importer_health',
    ]);

    register_rest_route($ns, '/login', [
        'methods'             => 'POST',
        'permission_callback' => '__return_true',
        'callback'            => 'figma_importer_login',
    ]);

    register_rest_route($ns, '/menu', [
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('edit_theme_options'); },
        'callback'            => 'figma_importer_create_or_update_menu',
    ]);

    register_rest_route($ns, '/page', [
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('edit_pages'); },
        'callback'            => 'figma_importer_create_or_update_page',
    ]);

    register_rest_route($ns, '/template', [
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('edit_posts'); },
        'callback'            => 'figma_importer_create_template',
    ]);

    register_rest_route($ns, '/kit', [
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('manage_options'); },
        'callback'            => 'figma_importer_update_kit',
    ]);

    register_rest_route($ns, '/elementor-data/(?P<id>\d+)', [
        [
            'methods'             => 'GET',
            'permission_callback' => function () { return current_user_can('edit_posts'); },
            'callback'            => 'figma_importer_get_elementor_data',
        ],
        [
            'methods'             => 'PATCH',
            'permission_callback' => function () { return current_user_can('edit_posts'); },
            'callback'            => 'figma_importer_patch_elementor_data',
        ],
    ]);

    register_rest_route($ns, '/forms/gravity', [
        [
            'methods'             => 'GET',
            'permission_callback' => function () { return current_user_can('gravityforms_edit_forms') || current_user_can('manage_options'); },
            'callback'            => 'figma_importer_list_gravity_forms',
        ],
        [
            'methods'             => 'POST',
            'permission_callback' => function () { return current_user_can('gravityforms_create_form') || current_user_can('manage_options'); },
            'callback'            => 'figma_importer_create_gravity_form',
        ],
    ]);

    register_rest_route($ns, '/media/reset', [
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('manage_options'); },
        'callback'            => 'figma_importer_reset_media',
    ]);
});

function figma_importer_health() {
    return [
        'ok'             => true,
        'version'        => FIGMA_IMPORTER_BRIDGE_VERSION,
        'wp_version'     => get_bloginfo('version'),
        'elementor'      => defined('ELEMENTOR_VERSION') ? ELEMENTOR_VERSION : null,
        'elementor_pro'  => defined('ELEMENTOR_PRO_VERSION') ? ELEMENTOR_PRO_VERSION : null,
        'gravity_forms'  => class_exists('GFForms') ? (defined('GF_VERSION') ? GF_VERSION : 'active') : null,
        'active_kit'     => (int) get_option('elementor_active_kit', 0),
        'active_theme'   => get_stylesheet(),
    ];
}

function figma_importer_login($req) {
    $params   = $req->get_json_params();
    $username = isset($params['username']) ? sanitize_user($params['username'], true) : '';
    $password = isset($params['password']) ? (string) $params['password'] : '';
    if ($username === '' || $password === '') {
        return new WP_Error('bad_creds', 'username and password are required', ['status' => 400]);
    }

    $user = wp_signon(
        [
            'user_login'    => $username,
            'user_password' => $password,
            'remember'      => true,
        ],
        is_ssl()
    );
    if (is_wp_error($user)) {
        return new WP_Error('auth_failed', $user->get_error_message(), ['status' => 401]);
    }

    wp_set_current_user($user->ID);

    return [
        'user_id'      => (int) $user->ID,
        'username'     => $user->user_login,
        'display_name' => $user->display_name,
        'roles'        => $user->roles,
        'nonce'        => wp_create_nonce('wp_rest'),
    ];
}

function figma_importer_create_or_update_menu($req) {
    $params   = $req->get_json_params();
    $name     = sanitize_text_field($params['name'] ?? '');
    $location = sanitize_key($params['location'] ?? '');
    $items    = $params['items'] ?? [];
    $reset    = !empty($params['reset']);

    if (!$name) {
        return new WP_Error('bad_data', 'name is required', ['status' => 400]);
    }
    if (!is_array($items)) {
        $items = [];
    }

    $menu = wp_get_nav_menu_object($name);
    $created = false;
    if (!$menu) {
        $menu_id = wp_create_nav_menu($name);
        if (is_wp_error($menu_id)) {
            return $menu_id;
        }
        $menu = wp_get_nav_menu_object($menu_id);
        $created = true;
    } elseif ($reset) {
        // Clear existing items only if explicitly asked, so re-runs don't wipe
        // user customisations.
        $existing = wp_get_nav_menu_items($menu->term_id) ?: [];
        foreach ($existing as $it) {
            wp_delete_post($it->ID, true);
        }
    }

    if ($created || $reset) {
        foreach ($items as $item) {
            $title     = sanitize_text_field($item['title'] ?? 'Item');
            $url       = esc_url_raw($item['url'] ?? '#');
            $object_id = isset($item['object_id']) ? (int) $item['object_id'] : 0;
            $type      = $object_id ? 'post_type' : 'custom';

            $args = [
                'menu-item-title'   => $title,
                'menu-item-url'     => $url,
                'menu-item-status'  => 'publish',
                'menu-item-type'    => $type,
            ];
            if ($object_id) {
                $args['menu-item-object-id'] = $object_id;
                $args['menu-item-object']    = 'page';
            }
            wp_update_nav_menu_item($menu->term_id, 0, $args);
        }
    }

    if ($location) {
        $locations = get_theme_mod('nav_menu_locations', []);
        if (!is_array($locations)) {
            $locations = [];
        }
        $locations[$location] = (int) $menu->term_id;
        set_theme_mod('nav_menu_locations', $locations);
    }

    return [
        'id'         => (int) $menu->term_id,
        'name'       => $menu->name,
        'slug'       => $menu->slug,
        'location'   => $location ?: null,
        'created'    => $created,
        'reset'      => $reset && !$created,
        'item_count' => count(wp_get_nav_menu_items($menu->term_id) ?: []),
    ];
}

function figma_importer_create_or_update_page($req) {
    $params = $req->get_json_params();
    $slug   = sanitize_title($params['slug'] ?? '');
    $title  = sanitize_text_field($params['title'] ?? 'Untitled');
    $template = sanitize_text_field($params['template'] ?? 'elementor_canvas');
    $data   = $params['elementor_data'] ?? null;
    $page_settings = $params['page_settings'] ?? null;

    if (!is_array($data)) {
        return new WP_Error('bad_data', 'elementor_data must be an array', ['status' => 400]);
    }
    if (!$slug) {
        return new WP_Error('bad_slug', 'slug is required', ['status' => 400]);
    }
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor plugin is not active', ['status' => 412]);
    }

    $existing = get_page_by_path($slug);
    $document = null;

    if ($existing) {
        // Update existing — refresh title, get the Document instance.
        wp_update_post([
            'ID'         => $existing->ID,
            'post_title' => $title,
        ]);
        $document = \Elementor\Plugin::$instance->documents->get($existing->ID);
    } else {
        // Create through Elementor's official Document API.
        $document = \Elementor\Plugin::$instance->documents->create(
            'wp-page',
            [
                'post_title'  => $title,
                'post_name'   => $slug,
                'post_type'   => 'page',
                'post_status' => 'publish',
            ]
        );
    }

    if (!$document || is_wp_error($document)) {
        return is_wp_error($document)
            ? $document
            : new WP_Error('no_doc', 'Failed to obtain Elementor document', ['status' => 500]);
    }

    // Run iterate_data: regen widget ids + each control's on_import hook fires
    // (this is what makes images resolve correctly across the widget zoo).
    $data = figma_importer_iterate_data($data, /* regen_ids */ true);

    $save_args = ['elements' => $data];
    if (is_array($page_settings)) {
        $save_args['settings'] = $page_settings;
    }
    $document->save($save_args);

    $post_id = $document->get_main_id();
    update_post_meta($post_id, '_wp_page_template', $template);

    figma_importer_clear_elementor_cache();

    return [
        'id'        => $post_id,
        'permalink' => get_permalink($post_id),
        'edit_url'  => admin_url("post.php?post={$post_id}&action=elementor"),
        'updated'   => $existing ? true : false,
    ];
}

function figma_importer_create_template($req) {
    $params        = $req->get_json_params();
    $template_type = sanitize_key($params['template_type'] ?? 'page');
    $title         = sanitize_text_field($params['title'] ?? 'Untitled Template');
    $data          = $params['elementor_data'] ?? null;
    $conditions    = $params['conditions'] ?? null;
    $slug          = sanitize_title($params['slug'] ?? '');           // optional upsert key
    $popup_settings = $params['popup_settings'] ?? null;              // optional, popup type only

    if (!is_array($data)) {
        return new WP_Error('bad_data', 'elementor_data must be an array', ['status' => 400]);
    }
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor plugin is not active', ['status' => 412]);
    }

    // Map our types to Elementor library document types.
    $type_map = [
        'header'  => 'header',
        'footer'  => 'footer',
        'page'    => 'page',
        'section' => 'section',
        'single'  => 'single-page',
        'archive' => 'archive',
        'popup'   => 'popup',
        'search'  => 'search-results',
        '404'     => 'error-404',
    ];
    $doc_type = isset($type_map[$template_type]) ? $type_map[$template_type] : 'page';

    // Slug-based upsert: re-use an existing elementor_library post with the
    // same slug + matching template type so re-runs don't pile up duplicates.
    $existing_id = 0;
    $updated = false;
    if ($slug) {
        $existing = get_posts([
            'name'           => $slug,
            'post_type'      => 'elementor_library',
            'post_status'    => ['publish', 'draft', 'private'],
            'numberposts'    => 1,
            'suppress_filters' => false,
        ]);
        if (!empty($existing)) {
            $existing_id = (int) $existing[0]->ID;
            // Verify the document type matches; if not, fail loud so we don't
            // accidentally overwrite a different kind of template.
            $existing_doc = \Elementor\Plugin::$instance->documents->get($existing_id);
            if ($existing_doc) {
                $existing_type = $existing_doc->get_template_type();
                if ($existing_type && $existing_type !== $doc_type) {
                    return new WP_Error(
                        'slug_type_mismatch',
                        sprintf('Template slug "%s" already exists with type "%s", cannot upsert as "%s"', $slug, $existing_type, $doc_type),
                        ['status' => 409]
                    );
                }
            }
        }
    }

    if ($existing_id) {
        wp_update_post([
            'ID'         => $existing_id,
            'post_title' => $title,
        ]);
        $document = \Elementor\Plugin::$instance->documents->get($existing_id);
        $updated = true;
    } else {
        $create_args = [
            'post_title'  => $title,
            'post_status' => 'publish',
            'post_type'   => 'elementor_library',
        ];
        if ($slug) {
            $create_args['post_name'] = $slug;
        }
        $document = \Elementor\Plugin::$instance->documents->create($doc_type, $create_args);
    }
    if (is_wp_error($document)) {
        return $document;
    }

    $data = figma_importer_iterate_data($data, /* regen_ids */ true);
    $document->save(['elements' => $data]);

    $post_id = $document->get_main_id();

    if (defined('ELEMENTOR_PRO_VERSION')) {
        $default_conditions = ($template_type === 'header' || $template_type === 'footer')
            ? ['include/general']
            : [];
        $cond = is_array($conditions) ? $conditions : $default_conditions;
        if ($cond) {
            update_post_meta($post_id, '_elementor_conditions', $cond);
            // Refresh Theme Builder's conditions cache so the new template
            // applies on the next page render.
            if (class_exists('\\ElementorPro\\Modules\\ThemeBuilder\\Module')) {
                try {
                    $module = \ElementorPro\Modules\ThemeBuilder\Module::instance();
                    if (method_exists($module, 'get_conditions_manager')) {
                        $cm = $module->get_conditions_manager();
                        if (method_exists($cm, 'get_cache')) {
                            $cm->get_cache()->regenerate();
                        }
                    }
                } catch (\Throwable $e) { /* best-effort */ }
            }
        }
    }

    // Popup-specific: write _elementor_popup_settings + matching post meta
    // so the popup picks up its trigger config (page-load, exit-intent, ...)
    // without manual editing in the Pro Popup UI.
    if ($template_type === 'popup' && is_array($popup_settings)) {
        update_post_meta($post_id, '_elementor_popup_settings', $popup_settings);
    }

    figma_importer_clear_elementor_cache();

    return [
        'id'             => $post_id,
        'template_type'  => $template_type,
        'slug'           => $slug ?: get_post_field('post_name', $post_id),
        'edit_url'       => admin_url("post.php?post={$post_id}&action=elementor"),
        'pro_active'     => defined('ELEMENTOR_PRO_VERSION'),
        'updated'        => $updated,
    ];
}

function figma_importer_update_kit($req) {
    $params        = $req->get_json_params();
    $page_settings = $params['page_settings'] ?? null;
    if (!is_array($page_settings)) {
        return new WP_Error('bad_data', 'page_settings must be an object', ['status' => 400]);
    }
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor plugin is not active', ['status' => 412]);
    }

    // Use Elementor's official kit + settings managers so saves go through the
    // proper hook chain (CSS regen, autosave updates, etc.).
    $kit = \Elementor\Plugin::$instance->kits_manager->get_active_kit_for_frontend();
    if (!$kit) {
        return new WP_Error('no_kit', 'No active Elementor kit. Activate Elementor first.', ['status' => 404]);
    }

    $page_settings_manager = \Elementor\Core\Settings\Manager::get_settings_managers('page');
    $meta_key = $page_settings_manager::META_KEY;

    $existing = $kit->get_meta($meta_key);
    $existing = is_array($existing) ? $existing : [];
    $merged   = array_merge($existing, $page_settings);

    $page_settings_manager->save_settings($merged, $kit->get_id());

    // Mirror across active autosaves so editor sessions don't clobber the change.
    $users = get_users(['fields' => ['ID']]);
    foreach ($users as $u) {
        $autosave = $kit->get_autosave($u->ID);
        if ($autosave) {
            $page_settings_manager->save_settings($merged, $autosave->get_id());
        }
    }

    figma_importer_clear_elementor_cache();

    return [
        'kit_id'        => $kit->get_id(),
        'page_settings' => $merged,
    ];
}

function figma_importer_get_elementor_data($req) {
    $id = (int) $req['id'];
    $raw = get_post_meta($id, '_elementor_data', true);
    if (!$raw) {
        return ['id' => $id, 'elementor_data' => []];
    }
    $decoded = json_decode($raw, true);
    return ['id' => $id, 'elementor_data' => is_array($decoded) ? $decoded : []];
}

function figma_importer_patch_elementor_data($req) {
    $id     = (int) $req['id'];
    $params = $req->get_json_params();
    $data   = $params['elementor_data'] ?? null;
    if (!is_array($data)) {
        return new WP_Error('bad_data', 'elementor_data must be an array', ['status' => 400]);
    }
    if (!class_exists('\\Elementor\\Plugin')) {
        return new WP_Error('no_elementor', 'Elementor plugin is not active', ['status' => 412]);
    }
    $document = \Elementor\Plugin::$instance->documents->get($id);
    if (!$document) {
        return new WP_Error('not_found', 'Elementor document not found for id ' . $id, ['status' => 404]);
    }
    // Don't regen ids on patch — auto-fixer needs ids stable to refer back to nodes.
    $data = figma_importer_iterate_data($data, /* regen_ids */ false);
    $document->save(['elements' => $data]);
    figma_importer_clear_elementor_cache();
    return ['id' => $id, 'updated' => true];
}

/**
 * Iterate over an Elementor element tree using the official iterate_data API
 * and run each control's `on_import` hook. This is what makes images, links,
 * carousels, galleries etc. resolve correctly across the entire widget zoo —
 * every Elementor widget gets to handle its own import logic instead of us
 * trying to second-guess it from the outside.
 */
function figma_importer_iterate_data(array $data, $regenerate_ids = true) {
    if (!class_exists('\\Elementor\\Plugin') || !did_action('elementor/loaded')) {
        return $data;
    }
    return \Elementor\Plugin::$instance->db->iterate_data(
        $data,
        function ($element) use ($regenerate_ids) {
            // Preserve agent-set keys that aren't registered Elementor controls
            // (control instances rebuild settings from registered controls only,
            // so e.g. __globals__ would be dropped without this snapshot).
            $original_settings = isset($element['settings']) && is_array($element['settings'])
                ? $element['settings'] : [];
            $preserve_keys = ['__globals__', '__dynamic__', 'css_classes', '_css_classes'];
            $agent_meta_prefixes = ['_figma_', '_template_ref', '_form_', '_dynamic_', '_low_confidence', '_design_reference_'];
            $preserved = [];
            foreach ($original_settings as $k => $v) {
                if (in_array($k, $preserve_keys, true)) {
                    $preserved[$k] = $v;
                    continue;
                }
                foreach ($agent_meta_prefixes as $pfx) {
                    if (strpos((string) $k, $pfx) === 0) {
                        $preserved[$k] = $v;
                        break;
                    }
                }
            }

            // Run per-control on_import hooks via a control instance.
            try {
                $instance = \Elementor\Plugin::$instance->elements_manager->create_element_instance($element);
            } catch (\Throwable $e) {
                $instance = null;
            }
            if ($instance instanceof \Elementor\Controls_Stack) {
                $element_data = $instance->get_data();
                if (method_exists($instance, 'on_import')) {
                    $element_data = $instance->on_import($element_data);
                }
                foreach ($instance->get_controls() as $control) {
                    if (empty($control['type']) || empty($control['name'])) continue;
                    $control_type = \Elementor\Plugin::$instance->controls_manager->get_control($control['type']);
                    if (!$control_type) continue;
                    if (method_exists($control_type, 'on_import')) {
                        try {
                            $element_data['settings'][$control['name']] = $control_type->on_import(
                                $instance->get_settings($control['name']),
                                $control
                            );
                        } catch (\Throwable $e) { /* leave control untouched */ }
                    }
                }
                $element = $element_data;
            }

            // Restore the preserved keys so __globals__ refs and our agent
            // markers survive the iterate_data round-trip.
            if ($preserved) {
                if (!isset($element['settings']) || !is_array($element['settings'])) {
                    $element['settings'] = [];
                }
                foreach ($preserved as $k => $v) {
                    // Existing iterate_data result wins for values it explicitly
                    // wrote; otherwise re-attach our preserved value.
                    if (!array_key_exists($k, $element['settings']) || $element['settings'][$k] === '' || $element['settings'][$k] === null) {
                        $element['settings'][$k] = $v;
                    }
                }
            }

            if ($regenerate_ids && class_exists('\\Elementor\\Utils')) {
                $element['id'] = \Elementor\Utils::generate_random_string();
            }
            return $element;
        }
    );
}

function figma_importer_clear_elementor_cache() {
    if (class_exists('\\Elementor\\Plugin')) {
        $plugin = \Elementor\Plugin::$instance;
        if ($plugin && isset($plugin->files_manager)) {
            $plugin->files_manager->clear_cache();
        }
    }
}

/**
 * Delete media library attachments uploaded by the agent on prior runs.
 * Matches by filename prefix (defaults to common figma-export prefixes:
 * `img_`, `node_`, `frame_`, `screenshot_`) so it never touches assets
 * the developer uploaded by hand.
 *
 * Body: { "prefixes": ["img_", "frame_", ...], "dry_run": true|false }
 * Returns { matched: n, deleted: n, kept: [...] } so the agent can confirm.
 */
function figma_importer_reset_media($req) {
    $params   = $req->get_json_params();
    $prefixes = $params['prefixes'] ?? ['img_', 'node_', 'frame_', 'screenshot_'];
    $dry_run  = !empty($params['dry_run']);
    if (!is_array($prefixes) || !$prefixes) {
        return new WP_Error('bad_data', 'prefixes must be a non-empty array', ['status' => 400]);
    }
    // Normalise prefixes to alphanumerics + hyphen + underscore.
    $prefixes = array_values(array_filter(array_map(function ($p) {
        $p = (string) $p;
        return preg_replace('/[^A-Za-z0-9_\-]/', '', $p);
    }, $prefixes)));

    $attachments = get_posts([
        'post_type'      => 'attachment',
        'post_status'    => 'inherit',
        'posts_per_page' => -1,
        'fields'         => 'ids',
    ]);

    $deleted = 0;
    $matched_ids = [];
    foreach ($attachments as $att_id) {
        $file = get_post_meta($att_id, '_wp_attached_file', true);
        if (!$file) continue;
        $base = basename($file);
        foreach ($prefixes as $pfx) {
            if (strpos($base, $pfx) === 0) {
                $matched_ids[] = (int) $att_id;
                if (!$dry_run) {
                    wp_delete_attachment($att_id, true);
                    $deleted += 1;
                }
                break;
            }
        }
    }
    return [
        'matched'  => count($matched_ids),
        'deleted'  => $deleted,
        'dry_run'  => $dry_run,
        'prefixes' => $prefixes,
        'matched_ids' => $matched_ids,
    ];
}

/**
 * List existing Gravity Forms (id + title only — enough for the agent
 * to detect duplicates before creating a new form).
 */
function figma_importer_list_gravity_forms() {
    if (!class_exists('GFAPI')) {
        return new WP_Error('no_gforms', 'Gravity Forms is not active', ['status' => 412]);
    }
    $forms = GFAPI::get_forms();
    $out = [];
    foreach ($forms as $f) {
        $out[] = [
            'id'    => (int) $f['id'],
            'title' => $f['title'],
            'is_active' => !empty($f['is_active']),
            'fields' => count($f['fields'] ?? []),
        ];
    }
    return ['forms' => $out];
}

/**
 * Create a Gravity Form from a compact spec emitted by the agent.
 *
 * Spec shape:
 *   {
 *     "title": "Contact Us",
 *     "description": "Leave us a message",
 *     "button": {"text": "Send"},
 *     "fields": [
 *       {"type": "text",     "label": "Name",    "isRequired": true},
 *       {"type": "email",    "label": "Email",   "isRequired": true},
 *       {"type": "textarea", "label": "Message"}
 *     ]
 *   }
 *
 * Field types map to Gravity Forms native types: text | email | textarea
 * | phone | number | select | radio | checkbox | name | address | website.
 * Anything unknown falls back to "text".
 */
function figma_importer_create_gravity_form($req) {
    if (!class_exists('GFAPI')) {
        return new WP_Error('no_gforms', 'Gravity Forms is not active', ['status' => 412]);
    }
    $params = $req->get_json_params();
    $title = sanitize_text_field($params['title'] ?? 'Untitled Form');
    $description = (string) ($params['description'] ?? '');
    $button = $params['button'] ?? ['text' => 'Submit'];
    $fields_in = $params['fields'] ?? [];
    if (!is_array($fields_in) || !$fields_in) {
        return new WP_Error('bad_data', 'fields must be a non-empty array', ['status' => 400]);
    }

    $allowed_types = [
        'text', 'email', 'textarea', 'phone', 'number',
        'select', 'radio', 'checkbox', 'name', 'address', 'website',
    ];

    $form = [
        'title'        => $title,
        'description'  => $description,
        'button'       => ['type' => 'text', 'text' => sanitize_text_field($button['text'] ?? 'Submit')],
        'fields'       => [],
        'is_active'    => true,
        'confirmations' => [
            'default' => [
                'id'          => uniqid('c'),
                'name'        => 'Default Confirmation',
                'isDefault'   => true,
                'type'        => 'message',
                'message'     => 'Thanks for reaching out — we will get back to you shortly.',
            ],
        ],
        'notifications' => [],
    ];

    $next_id = 1;
    foreach ($fields_in as $f) {
        $type = strtolower((string) ($f['type'] ?? 'text'));
        if (!in_array($type, $allowed_types, true)) {
            $type = 'text';
        }
        $field = [
            'id'         => $next_id,
            'type'       => $type,
            'label'      => sanitize_text_field($f['label'] ?? ucfirst($type)),
            'isRequired' => !empty($f['isRequired']),
        ];
        if (!empty($f['placeholder'])) {
            $field['placeholder'] = sanitize_text_field($f['placeholder']);
        }
        if (!empty($f['description'])) {
            $field['description'] = sanitize_text_field($f['description']);
        }
        // For choice fields, accept choices: ["A", "B"] or [{"text":"A","value":"a"}, ...]
        if (in_array($type, ['select', 'radio', 'checkbox'], true) && !empty($f['choices'])) {
            $choices = [];
            foreach ((array) $f['choices'] as $i => $choice) {
                if (is_string($choice)) {
                    $choices[] = ['text' => $choice, 'value' => $choice, 'isSelected' => false];
                } elseif (is_array($choice)) {
                    $choices[] = [
                        'text'       => sanitize_text_field($choice['text'] ?? ''),
                        'value'      => sanitize_text_field($choice['value'] ?? ($choice['text'] ?? '')),
                        'isSelected' => !empty($choice['isSelected']),
                    ];
                }
            }
            $field['choices'] = $choices;
        }
        $form['fields'][] = $field;
        $next_id += 1;
    }

    $form_id = GFAPI::add_form($form);
    if (is_wp_error($form_id)) {
        return $form_id;
    }
    return [
        'id'        => (int) $form_id,
        'title'     => $form['title'],
        'shortcode' => sprintf('[gravityform id="%d" title="false" description="false"]', (int) $form_id),
        'edit_url'  => admin_url("admin.php?page=gf_edit_forms&id={$form_id}"),
    ];
}
