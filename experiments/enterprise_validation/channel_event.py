# Odoo server-action source. CHANNELS is supplied by the installer.
# Only native chatter is mirrored; reads and desktop approvals are not events here.
categories = {'sale.order': 'sales', 'purchase.order': 'purchase', 'stock.picking': 'inventory',
              'mrp.production': 'manufacturing', 'account.move': 'finance', 'account.payment': 'finance'}
for message in records:
    category = categories.get(message.model)
    if not category or not message.res_id:
        continue
    savepoint = env.cr.savepoint()
    try:
        document = env[message.model].sudo().browse(message.res_id).exists()
        channel_id = CHANNELS.get((document.company_id.id, category)) if document else None
        if not channel_id:
            continue
        subject = 'erp-chatter-event:%s' % message.id
        if env['mail.message'].sudo().search_count([
            ('model', '=', 'discuss.channel'), ('res_id', '=', channel_id), ('subject', '=', subject)
        ]):
            continue
        details = []
        for change in message.tracking_value_ids:
            if change.field_id.name in ('state', 'payment_state'):
                details.append('%s：%s → %s' % (change.field_id.field_description,
                               change.old_value_char or '空', change.new_value_char or '空'))
        event = '；'.join(details) or ('字段更新（详见原单日志）' if message.tracking_value_ids else
                 '内部备注' if message.subtype_id.internal else '业务消息')
        parts = [document.company_id.name, document.display_name, event,
                 'Odoo 写入账号：%s (%s)' % (message.create_uid.name, message.create_uid.login),
                 '日志署名：%s' % (message.author_id.name or '系统'),
                 '事件时间（UTC）：%s' % message.date]
        body = '<p>' + '</p><p>'.join(str(v).replace('&', '&amp;').replace('<', '&lt;')
                                    .replace('>', '&gt;') for v in parts) + '</p>'
        body += '<p><a href="/odoo/%s/%s">查看原单与日志</a> · 来源消息 #%s</p>' % (
            message.model, message.res_id, message.id)
        env['discuss.channel'].sudo().browse(channel_id).with_context(
            mail_create_nosubscribe=True, mail_notify_noemail=True
        ).message_post(body=body, body_is_html=True, subject=subject,
                       message_type='comment', subtype_xmlid='mail.mt_comment')
        savepoint.close(rollback=False)
    except Exception as error:
        savepoint.close(rollback=True)
        # A channel failure must not roll back a valid business operation.
        log('ERP channel mirror failed for mail.message %s: %s' % (message.id, error), level='error')
    finally:
        savepoint.close(rollback=True)
