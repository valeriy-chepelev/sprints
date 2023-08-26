from yandex_tracker_client import TrackerClient
from lxml import etree
import math
import argparse
import datetime


def indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def hrs_txt(h: int):
    """ Return days+hours string from hours
    values less than hour zeroed"""
    if h == 0:
        return '0'
    d = '' if h // 8 == 0 else str(h // 8) + ' days'
    s = '' if h % 8 == 0 else str(h % 8) + ' hours'
    return d if s == '' else d + ' ' + s


def get_iso_split(s, split):
    if split in s:
        n, s = s.split(split)
    else:
        n = 0
    if n == '':
        n = 0
    return int(n), s


def iso_hrs(s):
    if s is None:
        return 0

    # Remove prefix
    s = s.split('P')[-1]

    # Step through letter dividers
    weeks, s = get_iso_split(s, 'W')
    days, s = get_iso_split(s, 'D')
    _, s = get_iso_split(s, 'T')
    hours, s = get_iso_split(s, 'H')

    # Convert all to hours
    return (weeks * 5 + days) * 8 + hours


def connect(root):
    conn = root.find('Connection')
    assert conn is not None
    client = TrackerClient(conn.get('token'), conn.get('org'))
    assert client.myself is not None
    return client


def issues(client, sprint):
    return client.issues.find(filter={'sprint': sprint},
                              order=['project'])


def get_first_estimation(issue):
    """ Issue is tracker API reference
    return first issue estimation value in original ISO dt notation, or PT0H as default"""
    return next((f['to'] for log in issue.changelog for f in log.fields
                 if (f['field'].id == 'estimation' and iso_hrs(f['to']) > 0)), 'PT0H')


def get_task_sprint_spent(issue):
    sprint = issue.sprint[0].id
    start = next((log.updatedAt for log in issue.changelog for f in log.fields
                  if f['field'].id == 'sprint'
                  and f['to'] is not None and f['to'][0].id == sprint), issue.createdAt)
    start = datetime.datetime.strptime(start, '%Y-%m-%dT%H:%M:%S.%f%z')
    return sum(iso_hrs(log.duration) for log in issue.worklog
               if datetime.datetime.strptime(log.updatedAt, '%Y-%m-%dT%H:%M:%S.%f%z') >= start)


def start_task(root, sprint, issue, force_planned=False):
    spr = root.find('sprint[@name="%s"]' % sprint)
    if spr is None:
        spr = etree.SubElement(root, 'sprint')
        spr.set('name', sprint)
    key = issue.key
    task = spr.find('issue[@key="%s"]' % key)
    if task is None:
        task = etree.SubElement(spr, 'issue')
        task.set('key', key)
        task.set('originalestimate', str(
            iso_hrs(get_first_estimation(issue))))
    task.set('estimate', str(
        0 if (v := issue.estimation) is None else iso_hrs(v)))
    if len(issue.fixVersions) > 0 or force_planned:
        task.set('planned', '1')


def stop_task(root, sprint, issue, force_planned=False):
    spr = root.find('sprint[@name="%s"]' % sprint)
    assert spr is not None
    key = issue.key
    task = spr.find('issue[@key="%s"]' % key)
    if task is None:
        task = etree.SubElement(spr, 'issue')
        task.set('key', key)
        task.set('originalestimate', str(
            iso_hrs(get_first_estimation(issue))))
        task.set('estimate', str(
            0 if (v := issue.estimation) is None else iso_hrs(v)))
        if len(issue.fixVersions) > 0 or force_planned:
            task.set('planned', '1')
    task.set('spent', str(get_task_sprint_spent(issue)))
    if not is_open(issue):
        task.set('totalspent', str(0 if (v := issue.spent) is None else iso_hrs(v)))
        task.set('developed', '1')


def calc_sprint_start(root, sprint, capacity: int):
    assert capacity > 0
    spr = root.find('sprint[@name="%s"]' % sprint)
    if spr is None:
        return
    spr.set('planned_capacity', str(capacity))
    vol = sum([int(t.get('estimate')) for t in spr])
    planned_vol = sum([int(t.get('estimate')) for t in spr if t.get('planned')])
    spr.set('sprint_load', str(round(vol / capacity * 100)))
    spr.set('sprint_plan_load', str(round(planned_vol / capacity * 100)))
    spr.set('plan_rate', str(0 if vol == 0 else round(planned_vol / vol * 100)))


def calc_sprint_stop(root, sprint, capacity: int):
    assert capacity > 0
    spr = root.find('sprint[@name="%s"]' % sprint)
    assert spr is not None
    spr.set('capacity', str(capacity))
    planned_spent = sum([int(t.get('spent'))
                         for t in spr.findall('issue[@planned="1"]')])
    closed_planned_ore = sum([int(t.get('originalestimate'))
                              for t in spr.findall('issue[@planned="1"]') if t.get('developed')])
    planned_ore = sum([int(t.get('originalestimate'))
                       for t in spr.findall('issue[@planned="1"]')])
    # planned_count = sum([1 for t in spr.findall('issue[@planned="1"]')])
    closed_planned_count = sum([1
                                for t in spr.findall('issue[@planned="1"]') if t.get('developed')])
    sprint_spent = sum([int(t.get('spent')) for t in spr])
    rmse = sum([pow(int(t.get('totalspent')) - int(t.get('originalestimate')), 2)
                for t in spr.findall('issue[@planned="1"]') if t.get('developed')])
    rmse = 0.0 if closed_planned_count == 0 else math.sqrt(rmse / closed_planned_count)
    ava = sum([100 * int(t.get('originalestimate')) / v if
               (v := int(t.get('totalspent'))) > 0 else 100
               for t in spr.findall('issue[@planned="1"]') if t.get('developed')])
    ava = 0.0 if closed_planned_count == 0 else ava / closed_planned_count

    spr.set('closed', str(closed_planned_count))
    spr.set('planned_job', str(round(0 if sprint_spent == 0
                                     else 100 * planned_spent / sprint_spent)))
    spr.set('job_result', str(round(0 if planned_ore == 0
                                    else 100 * closed_planned_ore / planned_ore)))
    spr.set('rmse', str(round(rmse)))
    spr.set('ava', str(round(ava)))
    spr.set('verity', str(round(100 * sprint_spent / capacity)))


def sprint_report(root, sprint):
    print(f'Sprint "{sprint}" report')
    spr = root.find('sprint[@name="%s"]' % sprint)
    if spr is None:
        print('Not found.')
        return
    print(f'{len([t for t in spr])} total tasks')
    print('planned:')
    print(f'  - capacity {hrs_txt(int(spr.get("planned_capacity")))}')
    print(f'  - total load {spr.get("sprint_load")}%')
    print(f'  - plan load {spr.get("sprint_plan_load")}%')
    print(f'  - plan rate {spr.get("plan_rate")}%')
    if spr.get('capacity') is None:
        print('Sprint not finished.')
        print(f'Use "sprint stop "{sprint}" fact_days" to get more metrics.')
        return
    print('fact results:')
    print(f'  - capacity {hrs_txt(int(spr.get("capacity")))}')
    print(f'  - {spr.get("closed")} planned tasks finished')
    print(f'  - verity {spr.get("verity")}%')
    print(f'  - planned job {spr.get("planned_job")}%')
    print(f'  - job result {spr.get("job_result")}%')
    print(f'  - estimate average accuracy {spr.get("ava")}%')
    print(f'  - estimate mean error {hrs_txt(int(spr.get("rmse")))}')
    print('Sprint finished.')
    print(f'Use sprint report "{sprint}" to show report again.')


def is_open(issue):
    assert (t := issue.type.key) in ['task', 'bug']
    if t == 'task':
        return str(issue.status.key).lower() in ['backlog', 'inprogress',
                                                 'onhold', 'inreview']
    else:
        return str(issue.status.key).lower() in ['readyfordev', 'inprogress',
                                                 'onhold', 'inreview']


def main():
    parser = argparse.ArgumentParser(description='Yandex Tracker sprint metrics by VCh.')
    parser.add_argument('action', choices=['start', 'stop', 'report'], help='type of action')
    parser.add_argument('sprint', help='sprint name')
    parser.add_argument('capacity', type=int, nargs='?', default=0, help='sprint_capacity[days]')
    parser.add_argument('-f', default='sprints.xml', help='xml datastorage name, default "sprints.xml"')
    parser.add_argument('-p', action='store_true', help='mean all new tasks as planned')
    args = parser.parse_args()

    document = etree.parse(args.f)
    xml_root = document.getroot()
    client = connect(xml_root)

    if args.action == 'start':
        if args.capacity == 0:
            raise ValueError('Capacity parameter not present')
        for issue in issues(client, args.sprint):
            print('.', end='')
            start_task(xml_root, args.sprint, issue, args.p)
        calc_sprint_start(xml_root, args.sprint, args.capacity * 8)
    if args.action == 'stop':
        if args.capacity == 0:
            raise ValueError('Capacity parameter not present')
        for issue in issues(client, args.sprint):
            print('.', end='')
            stop_task(xml_root, args.sprint, issue, args.p)
        calc_sprint_stop(xml_root, args.sprint, args.capacity * 8)
    print()
    if args.action == 'report':
        pass

    sprint_report(xml_root, args.sprint)
    indent(xml_root)
    document.write(args.f, pretty_print=True, xml_declaration=True, encoding='utf-8')


if __name__ == '__main__':
    main()
