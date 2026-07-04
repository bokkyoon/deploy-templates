def _build_secrets_db(secrets_list):
    # Même logique que inject_secrets (roles/deploy) : valeurs converties en chaînes
    secrets_db = {}
    for secret in secrets_list or []:
        if isinstance(secret, dict) and 'name' in secret:
            secrets_db[secret['name']] = {
                k: str(v) if v is not None else ''
                for k, v in secret.items()
                if k != 'name'
            }
    return secrets_db


def _desired_env(module, secrets_db):
    # Environnement tel qu'il sera rendu dans le compose : valeurs en chaînes, secrets %CLE% résolus
    module_secrets = secrets_db.get(module.get('name'), {})
    resolved = {}
    for key, value in (module.get('environment') or {}).items():
        if (isinstance(value, str) and value.startswith('%') and value.endswith('%')
                and value[1:-1] in module_secrets):
            resolved[key] = module_secrets[value[1:-1]]
        else:
            # str() reproduit le rendu Jinja du template compose, y compris
            # None -> "None" et True -> "True"
            resolved[key] = str(value)
    return resolved


def _running_env(service):
    container_spec = ((service.get('Spec') or {}).get('TaskTemplate') or {}).get('ContainerSpec') or {}
    env = {}
    for entry in container_spec.get('Env') or []:
        key, _, value = entry.partition('=')
        env[key] = value
    # DEPLOYMENT_ID change à chaque run, il ne doit pas déclencher de redéploiement
    env.pop('DEPLOYMENT_ID', None)
    return env


def _running_image(service):
    container_spec = ((service.get('Spec') or {}).get('TaskTemplate') or {}).get('ContainerSpec') or {}
    return container_spec.get('Image')


def deploy_plan(modules, stack_services, project_name, secrets_list=None):
    """
    Détermine les modules à (re)déployer et la raison pour chacun :
    force_restart, nouveau module, image changée ou environnement modifié.
    Un module avec enable: false n'est jamais déployé ; s'il tourne déjà,
    son nom est ajouté à to_remove pour que le service soit arrêté.
    Retourne {'modules': [...], 'reasons': [...], 'to_remove': [...]}.
    """
    secrets_db = _build_secrets_db(secrets_list)
    services_by_name = {}
    for service in stack_services or []:
        name = (service.get('Spec') or {}).get('Name')
        if name:
            services_by_name[name] = service

    to_deploy = []
    to_remove = []
    reasons = []
    for module in modules or []:
        if not isinstance(module, dict):
            continue
        module_name = module.get('name')
        service = services_by_name.get('%s_%s' % (project_name, module_name))

        if not module.get('enable', True):
            if service is not None:
                to_remove.append(module_name)
                reasons.append('%s: désactivé (enable: false) -> arrêt du service' % module_name)
            continue

        reason = None
        if module.get('force_restart'):
            reason = 'force_restart'
        elif service is None:
            reason = 'nouveau module'
        elif module.get('image') != _running_image(service):
            reason = 'image changée (%s -> %s)' % (_running_image(service), module.get('image'))
        else:
            desired = _desired_env(module, secrets_db)
            running = _running_env(service)
            if desired != running:
                # On ne liste que les clés (jamais les valeurs, potentiellement secrètes)
                changed = sorted(k for k in set(desired) | set(running)
                                 if desired.get(k) != running.get(k))
                reason = 'environnement modifié (%s)' % ', '.join(changed)

        if reason:
            to_deploy.append(module)
            reasons.append('%s: %s' % (module_name, reason))

    return {'modules': to_deploy, 'reasons': reasons, 'to_remove': to_remove}


class FilterModule(object):
    def filters(self):
        return {'deploy_plan': deploy_plan}
