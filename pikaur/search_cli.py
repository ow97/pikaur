""" This file is licensed under GPLv3, see https://www.gnu.org/licenses/ """

import sys
from multiprocessing.pool import ThreadPool
from typing import Any, Dict, Tuple, List, Iterable, Union, Set

import pyalpm

from .i18n import _
from .core import DataType, PackageSource, return_exception
from .pprint import (
    print_stderr,
)
from .print_department import print_package_search_results
from .pacman import PackageDB, get_pkg_id, refresh_pkg_db
from .aur import (
    AURPackageInfo,
    aur_rpc_search_name_desc, get_all_aur_packages, get_all_aur_names,
)
from .args import parse_args
from .exceptions import AURError, SysExit


@return_exception
def aur_thread_worker(search_word):
    result = aur_rpc_search_name_desc(search_word)
    return search_word, result


@return_exception
def package_search_thread_repo(index: str, args: Dict[str, Any]) -> Tuple[str, List[Any]]:
    if args['query']:
        result = PackageDB.search_repo(
            args['query'], names_only=args['namesonly']
        )
        index = ' '.join((args['index'], args['query'], ))
    else:
        result = PackageDB.get_repo_list(quiet=True)
    return index, result


@return_exception
def package_search_thread_aur(args: Dict[str, Any]) -> Dict[str, Any]:
    if args['queries']:
        with ThreadPool() as pool:
            result = {}
            for thread_result in pool.map(aur_thread_worker, args['queries']):
                if isinstance(thread_result, Exception):
                    return {str(PackageSource.AUR): thread_result}
                query, query_result = thread_result
                result[query] = query_result
            pool.close()
            pool.join()
            if args['namesonly']:
                for subindex, subresult in result.items():
                    result[subindex] = [
                        pkg for pkg in subresult
                        if subindex in pkg.name
                    ]
    else:
        if args['quiet']:
            class TmpNameType(DataType):
                name = None
            result = {'all': [
                TmpNameType(name=name) for name in get_all_aur_names()
            ]}
        else:
            result = {'all': get_all_aur_packages()}
    return result


@return_exception
def package_search_thread_router(args: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    index = args['index']
    result: Any = None
    if index == PackageSource.LOCAL:
        result = {
            pkg_name: pkg.version
            for pkg_name, pkg in PackageDB.get_local_dict(quiet=True).items()
        }
    elif str(index).startswith(str(PackageSource.REPO)):
        index, result = package_search_thread_repo(index, args)
    elif index == PackageSource.AUR:
        result = package_search_thread_aur(args)
    if not args.get('quiet'):
        sys.stderr.write('#')
    return index, result


def join_search_results(
        all_aur_results: List[List[Union[AURPackageInfo, pyalpm.Package]]]
) -> Iterable[Union[AURPackageInfo, pyalpm.Package]]:
    aur_pkgs_nameset: Set[str] = set()
    for search_results in all_aur_results:
        new_aur_pkgs_nameset = set(get_pkg_id(result) for result in search_results)
        if aur_pkgs_nameset:
            aur_pkgs_nameset = aur_pkgs_nameset.intersection(new_aur_pkgs_nameset)
        else:
            aur_pkgs_nameset = new_aur_pkgs_nameset
    return {
        get_pkg_id(result): result
        for result in all_aur_results[0]
        if get_pkg_id(result) in aur_pkgs_nameset
    }.values()


def cli_search_packages() -> None:
    args = parse_args()
    refresh_pkg_db()
    search_query = args.positional or []
    REPO_ONLY = args.repo  # pylint: disable=invalid-name
    AUR_ONLY = args.aur  # pylint: disable=invalid-name
    if not args.quiet:
        progressbar_length = max(len(search_query), 1) + (not REPO_ONLY) + (not AUR_ONLY)
        sys.stderr.write(_("Searching... [{bar}]").format(bar='-' * progressbar_length))
        sys.stderr.write('\x1b[\bb' * (progressbar_length + 1))
    with ThreadPool() as pool:
        results = pool.map(package_search_thread_router, [
            {
                "index": PackageSource.LOCAL,
                "quiet": args.quiet,
            }
        ] + (
            [
                {
                    "index": str(PackageSource.REPO) + search_word,
                    "query": search_word,
                    "namesonly": args.namesonly,
                    "quiet": args.quiet,
                }
                for search_word in (search_query or [''])
            ] if not AUR_ONLY
            else []
        ) + (
            [
                {
                    "index": PackageSource.AUR,
                    "queries": search_query,
                    "namesonly": args.namesonly,
                    "quiet": args.quiet,
                }
            ] if not REPO_ONLY
            else []
        ))
        pool.close()
        pool.join()
    result = dict(results)
    for subresult in result.values():
        if isinstance(subresult, Exception):
            raise subresult
    if not args.quiet:
        sys.stderr.write('\n')

    local_pkgs_versions = result[PackageSource.LOCAL]
    if not AUR_ONLY:
        repo_result = join_search_results([
            r for k, r in result.items() if str(k).startswith(str(PackageSource.REPO))
        ])
        print_package_search_results(
            packages=repo_result,
            local_pkgs_versions=local_pkgs_versions
        )
    if not REPO_ONLY:
        for _key, query_result in result[PackageSource.AUR].items():
            if isinstance(query_result, AURError):
                print_stderr('AUR returned error: {}'.format(query_result))
                raise SysExit(121)
            if isinstance(query_result, Exception):
                raise query_result
        aur_result = join_search_results([
            r for k, r in result[PackageSource.AUR].items()
        ])
        print_package_search_results(
            packages=aur_result,
            local_pkgs_versions=local_pkgs_versions
        )
