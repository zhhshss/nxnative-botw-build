#ifndef NXK_RESOURCE_EXPORT_H
#define NXK_RESOURCE_EXPORT_H

#import <Foundation/Foundation.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <sys/stat.h>
#include <unistd.h>
#include "NXKResourcePaths.h"
#include "NXKZip.h"

static const uint64_t NXKResourceLimit=UINT64_C(2)*1024*1024*1024;
static const uint64_t NXKResourceReserve=UINT64_C(64)*1024*1024;
static const NSUInteger NXKResourceFileLimit=20000;
static NSString *const NXKResourceErrorDomain=@"NXKeyboardResourceExport";

@interface NXKResourceExporter : NSObject
@property(atomic,readonly) BOOL busy;
@property(atomic,readonly) double progress;
- (void)inspectWithBundle:(NSBundle *)bundle completion:(void(^)(NSDictionary *summary,NSError *error))completion;
- (void)exportWithCompletion:(void(^)(NSURL *archive,NSError *error))completion;
- (void)cancel;
@end

@interface NXKResourceFile : NSObject {
@public
    struct stat identity;
}
@property(nonatomic,copy) NSString *relativePath;
@property(nonatomic) BOOL shader;
@end
@implementation NXKResourceFile
@end

@interface NXKResourceExporter () {
    _Atomic bool _cancelled;
    int _rootFD;
    struct stat _rootIdentity;
}
@property(atomic,readwrite) BOOL busy;
@property(atomic,readwrite) double progress;
@property(nonatomic,strong) dispatch_queue_t queue;
@property(nonatomic,copy) NSArray<NXKResourceFile *> *files;
@property(nonatomic,copy) NSDictionary *summary;
@property(nonatomic,copy) NSString *rootPath;
@property(nonatomic) uint64_t totalBytes;
- (BOOL)claimOperation;
- (void)discardInspection;
- (BOOL)isCancelled;
@end

static NSError *NXKResourceError(NSInteger code,NSString *description) {
    return [NSError errorWithDomain:NXKResourceErrorDomain code:code
        userInfo:@{NSLocalizedDescriptionKey:description}];
}
static BOOL NXKResourceComponent(NSString *component) {
    if (!component.length || [component isEqual:@"."] || [component isEqual:@".."] ||
        [component rangeOfCharacterFromSet:[NSCharacterSet characterSetWithCharactersInString:@"/\\:"]].location!=NSNotFound) return NO;
    return [component rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location==NSNotFound;
}
static BOOL NXKResourceRelative(NSString *relative) {
    if (!relative.length || [relative hasPrefix:@"/"] || relative.length>2048) return NO;
    for (NSString *component in [relative componentsSeparatedByString:@"/"])
        if (!NXKResourceComponent(component)) return NO;
    return YES;
}
static BOOL NXKResourceLayoutAllowed(NSString *name) {
    if (!NXKResourceComponent(name) || [name hasPrefix:@"."]) return NO;
    NSString *lower=name.lowercaseString;
    if ([@[@"dmios",@"save",@"saves",@"account",@"accounts",@"keys",@"log",@"logs",@"prod.keys",@"title.keys"] containsObject:lower]) return NO;
    return ![@[@"keys",@"log",@"nso",@"nro",@"nca",@"nsp",@"ipa",@"dylib",@"pem",@"p12",@"mobileprovision"] containsObject:lower.pathExtension];
}
static BOOL NXKResourceAddSize(uint64_t *total,uint64_t amount) {
    if (UINT64_MAX-*total<amount) return NO;
    *total+=amount;
    return YES;
}
static BOOL NXKResourceSameFile(const struct stat *a,const struct stat *b) {
    return S_ISREG(b->st_mode) && a->st_dev==b->st_dev && a->st_ino==b->st_ino &&
        a->st_size==b->st_size && a->st_mtimespec.tv_sec==b->st_mtimespec.tv_sec &&
        a->st_mtimespec.tv_nsec==b->st_mtimespec.tv_nsec &&
        a->st_ctimespec.tv_sec==b->st_ctimespec.tv_sec && a->st_ctimespec.tv_nsec==b->st_ctimespec.tv_nsec;
}
/* Only the Foundation-provided system prefix is canonicalized. The bundle,
 * romfs root and every component beneath it are opened without following links. */
static int NXKResourceOpenDirectory(NSString *canonicalPath) {
    if (![canonicalPath hasPrefix:@"/"]) { errno=EINVAL; return -1; }
    int directory=open("/",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    for (NSString *component in [canonicalPath pathComponents]) {
        if ([component isEqual:@"/"]) continue;
        if (!NXKResourceComponent(component)) { close(directory); errno=EINVAL; return -1; }
        int next=openat(directory,component.fileSystemRepresentation,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        int failure=errno;
        close(directory);
        if (next<0) { errno=failure; return -1; }
        directory=next;
    }
    return directory;
}
static int NXKResourceOpenRelative(int root,NSString *relative,BOOL directoryOnly) {
    if (!NXKResourceRelative(relative)) { errno=EINVAL; return -1; }
    NSArray<NSString *> *components=[relative componentsSeparatedByString:@"/"];
    int directory=dup(root);
    if (directory<0) return -1;
    for (NSUInteger i=0;i<components.count;i++) {
        BOOL last=i+1==components.count;
        int flags=O_RDONLY|O_CLOEXEC|O_NOFOLLOW|((!last || directoryOnly)?O_DIRECTORY:O_NONBLOCK);
        int next=openat(directory,components[i].fileSystemRepresentation,flags);
        int failure=errno;
        close(directory);
        if (next<0) { errno=failure; return -1; }
        directory=next;
    }
    return directory;
}
static BOOL NXKResourceDescribeRelative(int root,NSString *relative,struct stat *result) {
    NSArray<NSString *> *components=[relative componentsSeparatedByString:@"/"];
    if (!NXKResourceRelative(relative)) { errno=EINVAL; return NO; }
    int directory=dup(root);
    if (directory<0) return NO;
    for (NSUInteger i=0;i+1<components.count;i++) {
        int next=openat(directory,components[i].fileSystemRepresentation,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        int failure=errno;
        close(directory);
        if (next<0) { errno=failure; return NO; }
        directory=next;
    }
    int rc=fstatat(directory,components.lastObject.fileSystemRepresentation,result,AT_SYMLINK_NOFOLLOW);
    int failure=errno;
    close(directory);
    errno=failure;
    return rc==0;
}
static uint64_t NXKResourceFreeSpace(NSString *directory) {
    NSDictionary *attributes=[NSFileManager.defaultManager attributesOfFileSystemForPath:directory error:nil];
    return [attributes[NSFileSystemFreeSize] unsignedLongLongValue];
}

typedef struct {
    void *exporter;
    uint64_t completed,total;
} NXKResourceProgressContext;
static bool NXKResourceZipProgress(void *context,uint64_t fileBytes,uint64_t fileTotal) {
    (void)fileTotal;
    NXKResourceProgressContext *state=context;
    NXKResourceExporter *exporter=(__bridge NXKResourceExporter *)state->exporter;
    if ([exporter isCancelled]) return false;
    exporter.progress=state->total?fmin(0.99,(double)(state->completed+fileBytes)/(double)state->total):0;
    return true;
}

@implementation NXKResourceExporter
- (instancetype)init {
    if ((self=[super init])) {
        _rootFD=-1;
        self.queue=dispatch_queue_create("org.nxkeyboard.resource-export",DISPATCH_QUEUE_SERIAL);
        atomic_init(&_cancelled,false);
    }
    return self;
}
- (void)dealloc { if (_rootFD>=0) close(_rootFD); }
- (BOOL)claimOperation {
    @synchronized(self) {
        if (self.busy) return NO;
        self.busy=YES;
        self.progress=0;
        atomic_store(&_cancelled,false);
        return YES;
    }
}
- (BOOL)isCancelled { return atomic_load(&_cancelled); }
- (void)cancel { atomic_store(&_cancelled,true); }
- (void)discardInspection {
    if (_rootFD>=0) close(_rootFD);
    _rootFD=-1;
    self.files=nil;
    self.summary=nil;
    self.rootPath=nil;
    self.totalBytes=0;
}
- (void)inspectWithBundle:(NSBundle *)bundle completion:(void(^)(NSDictionary *,NSError *))completion {
    if (![self claimOperation]) {
        dispatch_async(dispatch_get_main_queue(),^{ if (completion) completion(nil,NXKResourceError(1,@"A resource operation is already running.")); });
        return;
    }
    NSString *bundleID=[bundle.bundleIdentifier copy],*bundlePath=[bundle.bundlePath copy];
    dispatch_async(self.queue,^{ @autoreleasepool {
        [self discardInspection];
        NSError *error=nil;
        NSMutableArray<NXKResourceFile *> *files=[NSMutableArray array];
        NSMutableArray<NSString *> *missing=[NSMutableArray array],*rejected=[NSMutableArray array];
        uint64_t total=0,maximum=0;
        NSUInteger shaders=0,layouts=0;
        int bundleFD=-1,rootFD=-1;
        if (![bundleID isEqual:@"org.nxnative.01007ef00011e000"] || !bundlePath.length) {
            error=NXKResourceError(2,@"This resource inventory supports only the selected BOTW application.");
        }
        if (!error) {
            struct stat info;
            if (lstat(bundlePath.fileSystemRepresentation,&info) || !S_ISDIR(info.st_mode) || S_ISLNK(info.st_mode))
                error=NXKResourceError(3,@"The application bundle is not a regular directory.");
        }
        NSString *canonicalBundle=nil;
        if (!error) {
            char parent[PATH_MAX];
            if (!realpath(bundlePath.stringByDeletingLastPathComponent.fileSystemRepresentation,parent))
                error=NXKResourceError(3,@"The application resource location could not be resolved.");
            else canonicalBundle=[[@(parent) stringByAppendingPathComponent:bundlePath.lastPathComponent] stringByStandardizingPath];
        }
        if (!error) {
            bundleFD=NXKResourceOpenDirectory(canonicalBundle);
            if (bundleFD>=0) rootFD=openat(bundleFD,"romfs",O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
            if (bundleFD<0 || rootFD<0 || fstat(rootFD,&self->_rootIdentity))
                error=NXKResourceError(4,@"No usable romfs directory was found inside the application bundle.");
        }
        if (bundleFD>=0) close(bundleFD);
        NSMutableSet<NSString *> *seen=[NSMutableSet set];
        if (!error) for (unsigned i=0;i<NXK_RESOURCE_SHADER_PATH_COUNT;i++) {
            if ([self isCancelled]) { error=NXKResourceError(5,@"Resource inspection cancelled."); break; }
            NSString *relative=@(NXKResourceShaderPaths[i]);
            struct stat info;
            if (!NXKResourceDescribeRelative(rootFD,relative,&info)) {
                if (errno==ENOENT) [missing addObject:relative];
                else [rejected addObject:relative];
                continue;
            }
            if (!S_ISREG(info.st_mode) || S_ISLNK(info.st_mode) || info.st_size<0) { [rejected addObject:relative]; continue; }
            NXKResourceFile *file=[NXKResourceFile new];
            file.relativePath=relative; file.shader=YES; file->identity=info;
            [files addObject:file]; [seen addObject:relative];
            if (!NXKResourceAddSize(&total,(uint64_t)info.st_size)) { error=NXKResourceError(6,@"The resource inventory is too large."); break; }
            maximum=MAX(maximum,(uint64_t)info.st_size); shaders++;
        }
        if (!error) {
            NSMutableArray<NSString *> *directories=[NSMutableArray arrayWithObject:@"Layout"];
            NSUInteger entries=0;
            while (directories.count && !error) {
                if ([self isCancelled]) { error=NXKResourceError(5,@"Resource inspection cancelled."); break; }
                NSString *relativeDirectory=directories.lastObject;
                [directories removeLastObject];
                int directory=NXKResourceOpenRelative(rootFD,relativeDirectory,YES);
                if (directory<0) {
                    if (errno==ENOENT && [relativeDirectory isEqual:@"Layout"]) [missing addObject:@"Layout/"];
                    else [rejected addObject:relativeDirectory];
                    continue;
                }
                DIR *stream=fdopendir(directory);
                if (!stream) { close(directory); error=NXKResourceError(6,@"The Layout directory could not be enumerated."); break; }
                struct dirent *entry;
                for (;;) {
                    errno=0;
                    entry=readdir(stream);
                    if (!entry) {
                        if (errno) error=NXKResourceError(6,@"The Layout inventory could not be read completely.");
                        break;
                    }
                    if (!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
                    if (++entries>NXKResourceFileLimit || [self isCancelled]) {
                        error=NXKResourceError(6,[self isCancelled]?@"Resource inspection cancelled.":@"The Layout inventory exceeds its supported file count."); break;
                    }
                    NSString *name=[[NSString alloc] initWithBytes:entry->d_name length:strlen(entry->d_name) encoding:NSUTF8StringEncoding];
                    if (!NXKResourceLayoutAllowed(name)) { [rejected addObject:relativeDirectory]; continue; }
                    NSString *relative=[relativeDirectory stringByAppendingPathComponent:name];
                    if (!NXKResourceRelative(relative)) { [rejected addObject:relativeDirectory]; continue; }
                    struct stat info;
                    if (fstatat(dirfd(stream),entry->d_name,&info,AT_SYMLINK_NOFOLLOW) || S_ISLNK(info.st_mode)) {
                        [rejected addObject:relative]; continue;
                    }
                    if (S_ISDIR(info.st_mode)) {
                        if ([relative componentsSeparatedByString:@"/"].count>32) [rejected addObject:relative];
                        else [directories addObject:relative];
                    } else if (S_ISREG(info.st_mode) && info.st_size>=0) {
                        if ([seen containsObject:relative]) continue;
                        NXKResourceFile *file=[NXKResourceFile new];
                        file.relativePath=relative; file->identity=info;
                        [files addObject:file]; [seen addObject:relative];
                        if (!NXKResourceAddSize(&total,(uint64_t)info.st_size)) { error=NXKResourceError(6,@"The resource inventory is too large."); break; }
                        maximum=MAX(maximum,(uint64_t)info.st_size); layouts++;
                    } else [rejected addObject:relative];
                }
                closedir(stream);
            }
        }
        if (!error && [self isCancelled]) error=NXKResourceError(5,@"Resource inspection cancelled.");
        NSDictionary *summary=nil;
        if (!error) {
            NSString *documents=NSSearchPathForDirectoriesInDomains(NSDocumentDirectory,NSUserDomainMask,YES).firstObject;
            uint64_t free=documents?NXKResourceFreeSpace(documents):0;
            BOOL within=total<=NXKResourceLimit && files.count<=NXKResourceFileLimit;
            uint64_t required=total>UINT64_MAX-NXKResourceReserve?UINT64_MAX:total+NXKResourceReserve;
            BOOL enough=free>=required;
            BOOL canExport=files.count>0 && within && enough;
            summary=@{@"fileCount":@(files.count),@"totalBytes":@(total),@"maxFileBytes":@(maximum),
                @"limitBytes":@(NXKResourceLimit),@"fileLimit":@(NXKResourceFileLimit),
                @"requiredFreeBytes":@(required),@"availableFreeBytes":@(free),
                @"shaderFileCount":@(shaders),@"layoutFileCount":@(layouts),
                @"missing":[missing sortedArrayUsingSelector:@selector(compare:)],
                @"rejected":[rejected sortedArrayUsingSelector:@selector(compare:)],
                @"partial":@(missing.count>0 || rejected.count>0),@"withinLimit":@(within),
                @"enoughSpace":@(enough),@"canExport":@(canExport)};
            self.files=[files sortedArrayUsingComparator:^NSComparisonResult(NXKResourceFile *a,NXKResourceFile *b) { return [a.relativePath compare:b.relativePath]; }];
            self.summary=summary; self.totalBytes=total;
            self.rootPath=[canonicalBundle stringByAppendingPathComponent:@"romfs"];
            self->_rootFD=rootFD; rootFD=-1;
        }
        if (rootFD>=0) close(rootFD);
        /* Keep the operation claimed until its preview reaches the UI; a new
         * inspection must not replace the snapshot behind an older preview. */
        dispatch_async(dispatch_get_main_queue(),^{ self.busy=NO; if (completion) completion(summary,error); });
    }});
}
- (void)exportWithCompletion:(void(^)(NSURL *,NSError *))completion {
    if (![self claimOperation]) {
        dispatch_async(dispatch_get_main_queue(),^{ if (completion) completion(nil,NXKResourceError(1,@"A resource operation is already running.")); });
        return;
    }
    dispatch_async(self.queue,^{ @autoreleasepool {
        NSError *error=nil;
        NSString *archivePath=nil,*manifestPath=nil;
        BOOL manifestCreated=NO,zipOpened=NO,zipFinished=NO;
        struct stat manifestIdentity={0},archiveIdentity={0};
        NXKZipWriter writer;
        if (![self.summary[@"canExport"] boolValue] || self->_rootFD<0 || !self.files.count)
            error=NXKResourceError(7,@"Inspect the resource inventory before exporting.");
        if (!error) {
            int currentRoot=NXKResourceOpenDirectory(self.rootPath);
            struct stat info;
            if (currentRoot<0 || fstat(currentRoot,&info) || info.st_dev!=self->_rootIdentity.st_dev || info.st_ino!=self->_rootIdentity.st_ino)
                error=NXKResourceError(8,@"The romfs directory changed. Inspect the resources again.");
            if (currentRoot>=0) close(currentRoot);
        }
        NSString *documents=NSSearchPathForDirectoriesInDomains(NSDocumentDirectory,NSUserDomainMask,YES).firstObject;
        if (!error && (!documents || NXKResourceFreeSpace(documents)<self.totalBytes+NXKResourceReserve))
            error=NXKResourceError(9,@"There is not enough free space for this resource export.");
        NSString *outputDirectory=nil;
        if (!error) {
            char canonicalDocuments[PATH_MAX];
            if (!realpath(documents.fileSystemRepresentation,canonicalDocuments)) error=NXKResourceError(9,@"The export folder is unavailable.");
            else {
                int documentsFD=NXKResourceOpenDirectory(@(canonicalDocuments));
                if (documentsFD<0) error=NXKResourceError(9,@"The export folder is unavailable.");
                else {
                    if (mkdirat(documentsFD,"NXKeyboardBuildResources",0700) && errno!=EEXIST)
                        error=NXKResourceError(9,@"The export folder could not be created.");
                    int outputFD=error?-1:openat(documentsFD,"NXKeyboardBuildResources",O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
                    if (!error && outputFD<0) error=NXKResourceError(9,@"The export folder must not be a symbolic link.");
                    if (outputFD>=0) close(outputFD);
                    close(documentsFD);
                }
                outputDirectory=[@(canonicalDocuments) stringByAppendingPathComponent:@"NXKeyboardBuildResources"];
            }
        }
        if (!error) {
            NSString *token=NSUUID.UUID.UUIDString;
            archivePath=[outputDirectory stringByAppendingPathComponent:[token stringByAppendingString:@".zip"]];
            manifestPath=[outputDirectory stringByAppendingPathComponent:[token stringByAppendingString:@".json"]];
            NSMutableArray *inventory=[NSMutableArray array];
            for (NXKResourceFile *file in self.files)
                [inventory addObject:@{@"path":[@"romfs/" stringByAppendingString:file.relativePath],@"bytes":@((uint64_t)file->identity.st_size),@"kind":file.shader?@"shader-source":@"layout"}];
            NSDictionary *manifest=@{@"schemaVersion":@1,@"game":@"01007EF00011E000",@"version":@"1.9.0",
                @"mainBuildID":@"cd57b23fa4bbad65803d9788c01821ee",
                @"inventorySource":@"seedds/nxnative@32b12b4de075f13fa8658b980f164c992e6136a1:shader_map.tsv",
                @"fileCount":@(self.files.count),@"totalBytes":@(self.totalBytes),@"partial":self.summary[@"partial"],
                @"missing":self.summary[@"missing"],@"rejected":self.summary[@"rejected"],@"files":inventory,
                @"scope":@"Selected shader-source files and Layout resources only. No executable, keys, saves, account data or logs."};
            NSData *data=[NSJSONSerialization dataWithJSONObject:manifest options:NSJSONWritingPrettyPrinted error:&error];
            int manifestFD=-1;
            if (data) manifestFD=open(manifestPath.fileSystemRepresentation,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
            if (manifestFD<0) error=NXKResourceError(10,@"The export manifest could not be created.");
            else {
                manifestCreated=YES;
                if (fstat(manifestFD,&manifestIdentity)) error=NXKResourceError(10,@"The export manifest could not be verified.");
                const uint8_t *bytes=data.bytes; NSUInteger written=0;
                while (written<data.length) {
                    ssize_t amount=write(manifestFD,bytes+written,data.length-written);
                    if (amount<0 && errno==EINTR) continue;
                    if (amount<=0) { error=NXKResourceError(10,@"The export manifest could not be written."); break; }
                    written+=(NSUInteger)amount;
                }
                close(manifestFD);
            }
        }
        if (!error && [self isCancelled]) error=NXKResourceError(5,@"Resource export cancelled.");
        if (!error) {
            zipOpened=nxk_zip_open(&writer,archivePath.fileSystemRepresentation);
            if (!zipOpened) error=NXKResourceError(11,@"The resource archive could not be created.");
            else { archiveIdentity.st_dev=writer.device; archiveIdentity.st_ino=writer.inode; }
        }
        NXKResourceProgressContext progress={(__bridge void *)self,0,self.totalBytes};
        if (!error) for (NXKResourceFile *file in self.files) {
            if ([self isCancelled]) { error=NXKResourceError(5,@"Resource export cancelled."); break; }
            int source=NXKResourceOpenRelative(self->_rootFD,file.relativePath,NO);
            struct stat current;
            if (source<0 || fstat(source,&current) || !NXKResourceSameFile(&file->identity,&current)) {
                if (source>=0) close(source);
                error=NXKResourceError(12,[@"Resource changed or is unavailable: " stringByAppendingString:file.relativePath]); break;
            }
            NSString *entry=[@"romfs/" stringByAppendingString:file.relativePath];
            BOOL added=nxk_zip_add_fd(&writer,source,entry.UTF8String,NXKResourceZipProgress,&progress);
            BOOL unchanged=!fstat(source,&current) && NXKResourceSameFile(&file->identity,&current);
            close(source);
            if (!added || !unchanged) {
                error=NXKResourceError(13,[self isCancelled]?@"Resource export cancelled.":[@"Could not export unchanged resource: " stringByAppendingString:file.relativePath]); break;
            }
            progress.completed+=(uint64_t)file->identity.st_size;
        }
        if (!error) {
            BOOL added=nxk_zip_add_file(&writer,manifestPath.fileSystemRepresentation,"manifest.json",NULL,NULL);
            if (!added || [self isCancelled]) error=NXKResourceError(14,[self isCancelled]?@"Resource export cancelled.":@"The archive manifest could not be stored.");
        }
        if (!error) {
            zipFinished=nxk_zip_close(&writer);
            if (!zipFinished) error=NXKResourceError(15,@"The resource archive could not be finalized.");
            else if ([self isCancelled]) {
                struct stat current;
                if (!lstat(archivePath.fileSystemRepresentation,&current) && S_ISREG(current.st_mode) &&
                    current.st_dev==archiveIdentity.st_dev && current.st_ino==archiveIdentity.st_ino) unlink(archivePath.fileSystemRepresentation);
                error=NXKResourceError(5,@"Resource export cancelled.");
            }
        }
        if (zipOpened && !zipFinished) nxk_zip_abort(&writer);
        if (manifestCreated) {
            struct stat current;
            if (!lstat(manifestPath.fileSystemRepresentation,&current) && S_ISREG(current.st_mode) &&
                current.st_dev==manifestIdentity.st_dev && current.st_ino==manifestIdentity.st_ino) unlink(manifestPath.fileSystemRepresentation);
        }
        if (!error) self.progress=1;
        else self.progress=0;
        if (error && error.code==12) [self discardInspection];
        NSURL *result=error?nil:[NSURL fileURLWithPath:archivePath];
        dispatch_async(dispatch_get_main_queue(),^{ self.busy=NO; if (completion) completion(result,error); });
    }});
}
@end

#endif
